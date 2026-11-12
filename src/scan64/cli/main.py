import argparse
import asyncio
import sys

from scan64.chess.opponents.bot_policy import DEFAULT_BOT_NODES
from scan64.cli.analyse import analyse_command
from scan64.cli.bots import calibrate_command


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan64 command line interface.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyse_parser = subparsers.add_parser(
        "analyse", help="Analyse PGN files and generate lessons."
    )
    analyse_parser.add_argument("files", nargs="+", help="PGN files to analyse.")
    analyse_parser.add_argument("--report", action="store_true", help="Generate a report.")

    bots_parser = subparsers.add_parser("bots", help="Inspect and calibrate the bot ladder.")
    bots_subparsers = bots_parser.add_subparsers(dest="bots_command", required=True)
    calibrate_parser = bots_subparsers.add_parser(
        "calibrate", help="Play and repeat 20 real games per adjacent bot-level pair."
    )
    calibrate_parser.add_argument("--stockfish", default="stockfish", help="Stockfish binary path.")
    calibrate_parser.add_argument(
        "--nodes",
        type=int,
        default=DEFAULT_BOT_NODES,
        help="Nodes per move; defaults to the production bot budget.",
    )

    args = parser.parse_args()

    if args.command == "analyse":
        asyncio.run(analyse_command(args.files, report=args.report))
    elif args.command == "bots" and args.bots_command == "calibrate":
        if args.nodes < 1:
            parser.error("--nodes must be positive")
        sys.exit(asyncio.run(calibrate_command(args.stockfish, nodes=args.nodes)))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
