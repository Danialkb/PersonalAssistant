import argparse

from pydantic import ValidationError

from jira.agent import AssistantAgent
from jira.settings import get_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Text-first personal assistant CLI.")
    parser.add_argument("prompt", nargs="*", help="Natural-language command, for example: достань мне задачи в jira")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    prompt = " ".join(args.prompt).strip()

    if not prompt:
        parser.error("Передайте текстовую команду, например: jira достань мне задачи в jira")

    try:
        settings = get_settings()
    except ValidationError as exc:
        missing = ", ".join(error["loc"][0] for error in exc.errors() if error["type"] == "missing")
        if missing:
            parser.exit(2, f"Не хватает переменных окружения: {missing}\n")
        raise

    agent = AssistantAgent(settings)
    print(agent.handle_text(prompt))
