"""Trello webhook adapter (createCard / updateCard events)."""
from typing import Any

from .base import NormalizedTask, SkipTaskException


class TrelloAdapter:
    """Maps Trello card-event payloads to a NormalizedTask.

    Cards must live in the configured list to be picked up.

    Sample payload subset::

        {
          "action": {
            "type": "createCard",
            "data": {
              "card": {"id": "abc", "name": "...", "desc": "Repository: ..."},
              "list": {"name": "ai-agent"}
            }
          }
        }
    """

    REQUIRED_LIST_NAME = "ai-agent"
    ACCEPTED_TYPES = ("createCard", "updateCard")

    @staticmethod
    def normalize(payload: dict[str, Any]) -> NormalizedTask:
        action = payload.get("action") or {}
        action_type = action.get("type")
        if action_type not in TrelloAdapter.ACCEPTED_TYPES:
            raise SkipTaskException(f"Ignoring Trello action: {action_type or '(missing)'}")

        data = action.get("data") or {}
        list_obj = data.get("list") or {}
        list_name = (list_obj.get("name") or "").strip().lower()
        if list_name != TrelloAdapter.REQUIRED_LIST_NAME:
            raise SkipTaskException(
                f"Trello card not in '{TrelloAdapter.REQUIRED_LIST_NAME}' list (was: '{list_name}')"
            )

        card = data.get("card") or {}
        card_id = card.get("id")
        title = card.get("name")
        description = card.get("desc") or ""

        if not (card_id and title and description):
            raise ValueError(
                "Missing required Trello fields: action.data.card.{id,name,desc}"
            )

        return NormalizedTask(
            task_id=f"TRELLO-{card_id}",
            title=str(title),
            description=str(description),
            source_meta={
                "trello_card_id": card_id,
                "trello_list": list_obj.get("name"),
                "trello_board": (data.get("board") or {}).get("name"),
            },
        )
