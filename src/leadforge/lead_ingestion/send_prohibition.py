"""The send and write prohibition at the ingestion boundary (11.1, 11.2, 11.3).

``Endpoint.read_only`` is ``Literal[True]``, so a write method cannot be declared. A
read-shaped method can still point at a send path (``POST /sequences/{id}/enroll``),
so a path is also checked against a denylist of the words a provider uses for sending,
enrolling, messaging and writing. The check is on whole words of the path, after
splitting on ``/ _ -`` and camelCase, so ``email-finder`` passes and ``single-send``
does not. The default is deny: any word on the list blocks a path.

Words are chosen from the provider endpoint families that send or write: sequence and
campaign enrolment, single-send and transactional email, engagement logging (email,
call, note, task), messaging, bulk create/import, record update/upsert/merge/delete,
webhook and subscription management, and opt-in/out writes. ``bulk`` alone is not
listed, because a bulk lookup is a read; ``bulk create`` is caught by ``create``.

A POST is only ever a read when its path is a search, match, lookup or verification
(``POST /contacts`` creates a record and carries no verb to match), so POST is default
deny: a POST path must be named in ``READ_ONLY_POST_ALLOWLIST`` with its reason, and
this is enforced when the adapter class is defined and again when a transport is built.
To add a legitimate read-only POST, add one entry (exact path, reason) to the allowlist
and nothing else; the guard test fails on an entry no adapter declares. The denylist
still applies to an allowlisted path.

Normalisation: the path is percent-decoded to a fixed point and lower-cased before it
is split, so ``/se%6Ed`` and ``/Send`` are caught. Whole-word tokens catch
``single-send``, ``single_send`` and ``singleSend``; a short list of long stems
(``send``, ``enroll``, ``campaign``...) is also matched inside a word, so the joined
``singlesend`` or ``sendemail`` is caught. A ``{placeholder}`` is a name filled at
request time, so one named like an action selector (``{action}``, ``{op}``) or a send
word is refused; any other placeholder is read as an identifier. Residual risk: a
value of an identifier placeholder cannot change the path's verb words, because the
transport encodes its slashes, so the worst it reaches is the same GET or allowlisted
POST; a verb the provider spells with no word listed here is not caught by the
denylist, which is why POST is default deny and the guard test also scans the source.
"""

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING
from urllib.parse import unquote

from leadforge.lead_ingestion.errors import SendCapableEndpointError

if TYPE_CHECKING:
    from leadforge.lead_ingestion.base_source import Endpoint

__all__ = [
    "READ_ONLY_POST_ALLOWLIST",
    "SELECTOR_PLACEHOLDERS",
    "SEND_PATH_STEMS",
    "SEND_PATH_TOKENS",
    "assert_no_send_capable_endpoints",
    "send_capable_reason",
]

SEND_PATH_TOKENS: frozenset[str] = frozenset(
    {
        # sending and messaging
        "send", "sends", "sender", "mailing", "mailings", "message", "messages",
        "messaging", "emails", "sms", "outreach", "invite", "invitation", "invitations",
        "connection", "connections", "mail", "mails", "mailer", "mailers", "inmail",
        "communication", "communications", "meeting", "meetings", "conversation",
        "conversations", "reply", "forward", "broadcast", "notification",
        "notifications", "recipient", "recipients", "drip",
        # sequence and campaign enrolment
        "sequence", "sequences", "campaign", "campaigns", "emailer", "emailers",
        "enroll", "enrol", "enrolls", "enrols", "enrollment", "enrollments",
        "enrolment", "enrolments", "trigger", "schedule", "launch", "dispatch",
        "publish",
        # engagement logging
        "engagement", "engagements", "call", "calls", "task", "tasks", "note",
        "notes",
        # record writes
        "create", "add", "update", "upsert", "merge", "import", "imports",
        "delete", "destroy", "remove", "archive", "restore", "submit", "insert",
        "write", "put", "patch",
        # webhook, subscription and opt-in/out writes
        "webhook", "webhooks", "subscribe", "unsubscribe", "subscription",
        "subscriptions", "optin", "optout",
    }
)  # fmt: skip

# Stems long and specific enough to match inside a word, so a joined spelling such as
# ``singlesend`` is caught. ``sequence`` is not one: ``consequence`` contains it.
SEND_PATH_STEMS: tuple[str, ...] = (
    "send", "enroll", "enrol", "campaign", "outreach", "mailer", "subscribe",
    "webhook", "optout",
)  # fmt: skip

# Placeholder names that choose the operation at request time, not an identifier.
SELECTOR_PLACEHOLDERS: frozenset[str] = frozenset(
    {"action", "actions", "operation", "op", "verb", "method", "command", "cmd",
     "endpoint", "function", "path"}
)  # fmt: skip

# Each POST an adapter may declare, and why it reads and writes nothing.
READ_ONLY_POST_ALLOWLIST: Mapping[str, str] = MappingProxyType(
    {
        "/api/v1/mixed_people/api_search": "people search; the query is in the body",
        "/api/v1/people/match": "match lookup; spends credits but writes nothing",
        "/crm/objects/{version}/contacts/search": "CRM search; the filter is a body",
        "/crm/objects/{version}/deals/search": "CRM search; the filter is a body",
    }
)

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_PLACEHOLDER = re.compile(r"\{([^}]*)\}")
_WORD = re.compile(r"[a-z0-9]+")
_DECODE_ROUNDS = 5
_REFUSED_PLACEHOLDER_WORDS = SEND_PATH_TOKENS | SELECTOR_PLACEHOLDERS


def _decoded(path: str) -> str:
    """``path`` percent-decoded until it stops changing (bounded): ``%2565`` is seen."""
    for _ in range(_DECODE_ROUNDS):
        plain = unquote(path)
        if plain == path:
            break
        path = plain
    return path


def _words(text: str) -> list[str]:
    return _WORD.findall(_CAMEL.sub(" ", text).lower())


def send_capable_reason(path: str) -> str | None:
    """Why ``path`` could send or write, or None if no word of it is denylisted."""
    text = _decoded(path)
    hits: set[str] = set()
    for name in _PLACEHOLDER.findall(text):
        hits.update(w for w in _words(name) if w in _REFUSED_PLACEHOLDER_WORDS)
    plain = _PLACEHOLDER.sub(" ", text)
    hits.update(w for w in _words(plain) if w in SEND_PATH_TOKENS)
    joined = plain.lower()
    hits.update(stem for stem in SEND_PATH_STEMS if stem in joined)
    return f"path names {', '.join(sorted(hits))}" if hits else None


def assert_no_send_capable_endpoints(
    provider: str, endpoints: Mapping[str, "Endpoint"]
) -> None:
    """Raise ``SendCapableEndpointError`` for the first send-capable endpoint.

    An endpoint is refused when its path names a send or write word, or when it is a
    POST whose path is not on ``READ_ONLY_POST_ALLOWLIST`` (default deny). Anything
    that is not an ``Endpoint`` is skipped: the contract's type check rejects it.
    """
    for endpoint in endpoints.values():
        path = getattr(endpoint, "path", None)
        if not isinstance(path, str):
            continue
        reason = send_capable_reason(path)
        if (
            reason is None
            and getattr(endpoint, "method", None) == "POST"
            and path not in READ_ONLY_POST_ALLOWLIST
        ):
            reason = "POST path is not on the read-only allowlist"
        if reason is not None:
            raise SendCapableEndpointError(provider, path=path, reason=reason)
