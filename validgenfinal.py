import requests
import concurrent.futures
import time
import logging
from datetime import datetime
import os
import sys
from rich.console import Console
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.text import Text
from rich.prompt import Prompt, Confirm
from rich import box
import pyfiglet

# Configuration
API_URL = "https://api.mullvad.net/www/accounts/v1/"
VALID_ACCOUNTS = "valid_accounts.txt"
LOG_FILE = "account_validator.log"

# Setup Rich console
console = Console()

# Setup logging with Rich handler
class RichLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.console = console

    def emit(self, record):
        try:
            msg = self.format(record)
            if record.levelno >= logging.ERROR:
                self.console.print(f"[red]\[ERROR][/red] {msg}")
            elif record.levelno >= logging.WARNING:
                self.console.print(f"[yellow]\[WARNING][/yellow] {msg}")
            elif record.levelno >= logging.INFO:
                self.console.print(f"[blue]\[INFO][/blue] {msg}")
            else:
                self.console.print(f"[white]\[DEBUG][/white] {msg}")
        except Exception:
            self.handleError(record)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        RichLogHandler()
    ]
)
logger = logging.getLogger(__name__)

def create_banner():
    """Create ASCII art banner with pyfiglet"""
    ascii_banner = pyfiglet.figlet_format("Account Validator", font="small")
    return Panel(
        Text(ascii_banner, style="bold cyan"),
        box=box.DOUBLE,
        style="bright_magenta"
    )

def setup_files():
    """Ensure output files exist and are empty at start"""
    with open(VALID_ACCOUNTS, "w") as f:
        f.write("")  # Clear file at start
    logger.info(f"Output files initialized. Results will be saved to {VALID_ACCOUNTS}")

class AccountValidator:
    def __init__(self):
        self.valid_count = 0
        self.checked_count = 0
        self.error_count = 0
        self.rate_limit_count = 0
        self.start_time = None
        self.fetch_workers = 10
        self.process_workers = 5
        self.range_start = 1000000000000000
        self.range_end = 1000000000009999
        self.is_running = False

    def create_stats_table(self):
        """Create a rich table for statistics"""
        if not self.start_time:
            return Panel("No validation process has been started yet", title="📊 Statistics", style="blue")

        elapsed = time.time() - self.start_time
        rate = self.checked_count / elapsed if elapsed > 0 else 0

        table = Table(show_header=True, header_style="bold magenta", box=box.ROUNDED)
        table.add_column("Metric", style="cyan", width=20)
        table.add_column("Value", style="white", justify="right")
        table.add_column("Status", style="green", width=15)

        table.add_row("Accounts Checked", f"{self.checked_count:,}", "✅" if self.checked_count > 0 else "⏳")
        table.add_row("Valid Accounts", f"{self.valid_count:,}", "🎯" if self.valid_count > 0 else "❌")
        table.add_row("Errors", f"{self.error_count:,}", "⚠️" if self.error_count > 0 else "✅")
        table.add_row("Rate Limits", f"{self.rate_limit_count:,}", "🚦" if self.rate_limit_count > 0 else "✅")
        table.add_row("Elapsed Time", f"{elapsed:.2f}s", "⏱️")
        table.add_row("Rate", f"{rate:.2f}/s", "📈" if rate > 0 else "📉")

        return Panel(table, title="📊 Validation Statistics", style="green")

    def create_config_table(self):
        """Create a rich table for current configuration"""
        table = Table(show_header=True, header_style="bold yellow", box=box.ROUNDED)
        table.add_column("Setting", style="cyan", width=20)
        table.add_column("Value", style="white")

        table.add_row("Range Start", f"{self.range_start:,}")
        table.add_row("Range End", f"{self.range_end:,}")
        table.add_row("Total Accounts", f"{(self.range_end - self.range_start + 1):,}")
        table.add_row("Fetch Workers", str(self.fetch_workers))
        table.add_row("Process Workers", str(self.process_workers))
        table.add_row("Status", "[green]Ready[/green]" if not self.is_running else "[red]Running[/red]")

        return Panel(table, title="⚙️ Current Configuration", style="yellow")

    def show_stats_panel(self):
        """Display statistics in a panel"""
        layout = Layout()
        layout.split_column(
            Layout(self.create_config_table(), name="config", size=10),
            Layout(self.create_stats_table(), name="stats")
        )
        console.print(layout)

    def set_custom_range(self):
        """Set custom account number range"""
        console.print("\n[bold cyan]Set Account Number Range[/bold cyan]")
        try:
            start = Prompt.ask("📝 Start (16 digits)", default=str(self.range_start))
            end = Prompt.ask("📝 End (16 digits)", default=str(self.range_end))

            start = int(start)
            end = int(end)

            if len(str(start)) != 16 or len(str(end)) != 16:
                console.print("❌ [red]Error: Account numbers must be 16 digits![/red]")
                return False

            if start >= end:
                console.print("❌ [red]Error: Start must be less than end![/red]")
                return False

            self.range_start = start
            self.range_end = end
            console.print(f"✅ [green]Range set to: {start:,} - {end:,}[/green]")
            return True

        except ValueError:
            console.print("❌ [red]Error: Please enter valid numbers![/red]")
            return False

    def configure_threads(self):
        """Configure thread workers"""
        console.print("\n[bold cyan]Configure Threads[/bold cyan]")
        try:
            fetch = Prompt.ask("🔧 Fetch workers", default=str(self.fetch_workers))
            process = Prompt.ask("🔧 Process workers", default=str(self.process_workers))

            fetch = int(fetch)
            process = int(process)

            self.fetch_workers = max(1, min(fetch, 50))
            self.process_workers = max(1, min(process, 20))

            console.print(f"✅ [green]Thread configuration updated: Fetch={self.fetch_workers}, Process={self.process_workers}[/green]")
            return True

        except ValueError:
            console.print("❌ [red]Error: Please enter valid numbers![/red]")
            return False

    def fetch_response(self, account_number):
        """Fetch the HTTP response for a given account number."""
        try:
            response = requests.get(f"{API_URL}{account_number}/", timeout=5)
            self.checked_count += 1
            return account_number, response.status_code, response.headers
        except requests.RequestException as e:
            self.error_count += 1
            logger.error(f"Error fetching account {account_number}: {e}")
            return account_number, None, None

    def process_response(self, account_number, status_code, headers=None):
        """Process the HTTP response to check if the account is valid."""
        try:
            if status_code == 200:
                with open(VALID_ACCOUNTS, "a") as file:
                    file.write(f"{account_number}\n")
                self.valid_count += 1
                logger.info(f"Valid account found: {account_number}")

            elif status_code == 429:
                self.rate_limit_count += 1
                logger.warning("Rate limit reached. Pausing for 10 seconds.")

            elif status_code == 404:
                pass  # Normal case - account doesn't exist

            else:
                logger.warning(f"Unexpected status {status_code} for account {account_number}")

        except Exception as e:
            self.error_count += 1
            logger.error(f"Error processing account {account_number}: {e}")

    def run_validation(self):
        """Run the main validation process"""
        setup_files()

        # Show configuration confirmation
        config_table = Table(box=box.ROUNDED, show_header=False)
        config_table.add_column("Setting", style="cyan")
        config_table.add_column("Value", style="white")

        config_table.add_row("Range", f"{self.range_start:,} - {self.range_end:,}")
        config_table.add_row("Total Accounts", f"{(self.range_end - self.range_start + 1):,}")
        config_table.add_row("Fetch Workers", str(self.fetch_workers))
        config_table.add_row("Process Workers", str(self.process_workers))
        config_table.add_row("Output File", VALID_ACCOUNTS)
        config_table.add_row("Log File", LOG_FILE)

        console.print(Panel(config_table, title="🚀 Validation Configuration", style="green"))

        if not Confirm.ask("\nStart validation?"):
            return

        self.is_running = True
        self.start_time = time.time()
        logger.info(f"Starting validation process with range {self.range_start}-{self.range_end}")

        try:
            # Create progress display
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console
            ) as progress:

                total_accounts = self.range_end - self.range_start + 1
                main_task = progress.add_task("[cyan]Validating accounts...", total=total_accounts)

                # Generate account numbers for the specified range
                account_numbers = (str(i) for i in range(self.range_start, self.range_end + 1))

                with concurrent.futures.ThreadPoolExecutor(max_workers=self.fetch_workers) as fetch_executor:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=self.process_workers) as process_executor:
                        # Submit all fetch tasks
                        fetch_futures = {
                            fetch_executor.submit(self.fetch_response, account): account
                            for account in account_numbers
                        }

                        # Process completed futures
                        for future in concurrent.futures.as_completed(fetch_futures):
                            try:
                                account_number, status_code, headers = future.result()
                                if status_code is not None:
                                    process_executor.submit(self.process_response, account_number, status_code, headers)

                                # Update progress
                                progress.update(main_task, advance=1)

                                # Show live updates every 100 accounts
                                if self.checked_count % 100 == 0:
                                    progress.console.print(
                                        f"[yellow]Progress: {self.checked_count:,} checked, "
                                        f"{self.valid_count:,} valid, "
                                        f"{self.error_count:,} errors[/yellow]"
                                    )

                            except Exception as e:
                                self.error_count += 1
                                logger.error(f"Future processing error: {e}")

        except KeyboardInterrupt:
            logger.info("Validation process interrupted by user")
            console.print("\n🛑 [red]Process interrupted by user.[/red]")
        except Exception as e:
            logger.error(f"Unexpected error in validation process: {e}")
            console.print(f"❌ [red]Unexpected error: {e}[/red]")
        finally:
            self.is_running = False
            elapsed = time.time() - self.start_time
            logger.info(f"Validation completed. Checked: {self.checked_count}, Valid: {self.valid_count}, Time: {elapsed:.2f}s")

            # Show final results
            results_table = Table(box=box.DOUBLE, show_header=True, header_style="bold green")
            results_table.add_column("Result", style="cyan")
            results_table.add_column("Count", style="white", justify="right")
            results_table.add_column("Percentage", style="yellow", justify="right")

            checked_pct = (self.checked_count / total_accounts * 100) if total_accounts > 0 else 0
            valid_pct = (self.valid_count / self.checked_count * 100) if self.checked_count > 0 else 0

            results_table.add_row("Accounts Checked", f"{self.checked_count:,}", f"{checked_pct:.1f}%")
            results_table.add_row("Valid Accounts", f"{self.valid_count:,}", f"{valid_pct:.1f}%")
            results_table.add_row("Errors", f"{self.error_count:,}", "-")
            results_table.add_row("Rate Limits", f"{self.rate_limit_count:,}", "-")
            results_table.add_row("Time Elapsed", f"{elapsed:.2f}s", f"{self.checked_count/elapsed:.2f}/s" if elapsed > 0 else "-")

            console.print(Panel(results_table, title="🎉 Validation Complete!", style="green"))

            if self.valid_count > 0:
                console.print(f"✅ [bold green]Valid accounts saved to: {VALID_ACCOUNTS}[/bold green]")

def main_menu():
    """Display main menu and handle user input"""
    validator = AccountValidator()

    while True:
        console.clear()
        console.print(create_banner())

        # Create menu table
        menu_table = Table(show_header=True, header_style="bold blue", box=box.ROUNDED, width=60)
        menu_table.add_column("Option", style="cyan", width=10)
        menu_table.add_column("Description", style="white")

        menu_table.add_row("1", "🚀 Start validation process")
        menu_table.add_row("2", "🎯 Set custom range")
        menu_table.add_row("3", "⚙️ Configure threads")
        menu_table.add_row("4", "📊 View statistics")
        menu_table.add_row("5", "❌ Exit")

        console.print(Panel(menu_table, title="📋 Main Menu", style="blue"))

        # Show current status
        validator.show_stats_panel()

        choice = Prompt.ask(
            "\nSelect option",
            choices=["1", "2", "3", "4", "5"],
            default="1"
        )

        if choice == '1':
            validator.run_validation()
            Prompt.ask("\nPress Enter to continue")

        elif choice == '2':
            validator.set_custom_range()
            Prompt.ask("\nPress Enter to continue")

        elif choice == '3':
            validator.configure_threads()
            Prompt.ask("\nPress Enter to continue")

        elif choice == '4':
            validator.show_stats_panel()
            Prompt.ask("\nPress Enter to continue")

        elif choice == '5':
            console.print("👋 [bold green]Goodbye![/bold green]")
            logger.info("Application closed by user")
            break

if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        console.print("\n👋 [bold green]Goodbye![/bold green]")
        logger.info("Application closed by keyboard interrupt")
    except Exception as e:
        console.print(f"❌ [red]Unexpected error: {e}[/red]")
        logger.error(f"Unexpected error in main: {e}")
