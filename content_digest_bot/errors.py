"""User-facing errors, kept separate from the ones that must not be shown.

Raw exception text reaches Telegram as-is unless something stops it, and it
routinely carries local filesystem paths, request URLs with query strings,
and library internals. None of that belongs in a chat message.

The rule here: only a message we wrote ourselves is ever shown. Everything
else becomes a generic line, and the real exception goes to the log.
"""


class BotError(Exception):
    """An error whose message was written for a human to read.

    Raise this when the cause is known and the wording is deliberately safe —
    "Ollama isn't reachable", "that file is too large". Anything else should
    raise a normal exception so it gets redacted on the way out.
    """


class TransientError(BotError):
    """A failure worth retrying on its own: a blip, a timeout, a rate limit.

    Separated from BotError because retrying the rest is pointless — a revoked
    key, an empty credit balance or a model that doesn't exist will fail
    exactly the same way the second time, and retrying just burns the user's
    time.
    """


GENERIC = "Something went wrong on my side. It's in the log."


def user_message(exc, fallback=GENERIC):
    """The safe, user-facing text for an exception.

    Curated BotError messages pass through; everything else is replaced, so an
    unexpected failure can't leak a path or a URL into the chat.
    """
    if isinstance(exc, BotError):
        text = str(exc).strip()
        if text:
            return text
    return fallback
