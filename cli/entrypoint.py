"""Stable console entry point that defers loading the interactive CLI."""


def main() -> None:
    from cli.main import main as run_cli

    run_cli()
