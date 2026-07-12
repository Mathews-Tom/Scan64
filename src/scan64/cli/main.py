import argparse
import asyncio
import sys

from scan64.cli.analyse import analyse_command


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan64 command line interface.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyse_parser = subparsers.add_parser(
        "analyse", help="Analyse PGN files and generate lessons."
    )
    analyse_parser.add_argument("files", nargs="+", help="PGN files to analyse.")
    analyse_parser.add_argument("--report", action="store_true", help="Generate a report.")

    args = parser.parse_args()

    if args.command == "analyse":
        asyncio.run(analyse_command(args.files, report=args.report))
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
