"""Post rules.

Shape mirrors the automation builder: trigger on some posts, when a comment
contains any of several keywords, optionally send an opening DM with a button,
then the main DM with link buttons.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.constants import MAX_CLONE_TARGETS

MatchMode = Literal["exact", "contains", "word"]
# "specific" = the listed posts. "any" = every post, including future ones.
RuleScope = Literal["specific", "any"]

# What starts an automation. "comment" is the original and remains the default,
# so rules written before this field existed keep their behaviour.
RuleTrigger = Literal["comment", "story_reply", "dm_keyword"]

MAX_KEYWORDS = 20
MAX_BUTTONS = 3


class Limits(BaseModel):
    """Application-side safety controls, not Meta's limits (§31)."""

    per_minute: int = Field(default=10, ge=1, le=1000)
    per_hour: int = Field(default=100, ge=1, le=10000)
    per_day: int = Field(default=500, ge=1, le=100000)


class LinkButton(BaseModel):
    """A web_url button on the DM. Meta caps the title at 20 characters."""

    title: str = Field(min_length=1, max_length=20)
    url: str = Field(min_length=1, max_length=2000)

    @field_validator("url")
    @classmethod
    def must_be_https(cls, value: str) -> str:
        # Meta rejects non-HTTPS web_url buttons.
        if not value.startswith("https://"):
            raise ValueError("button url must start with https://")
        return value


class OpeningDm(BaseModel):
    """First message, sent as the private reply.

    A private reply may be one message only, so the link cannot follow in the
    same breath. The button tap is a user-initiated interaction, which opens the
    24-hour window the main DM is sent in.
    """

    enabled: bool = False
    message: str = Field(default="", max_length=200)
    button_text: str = Field(default="Send me the link", max_length=20)


class PublicReply(BaseModel):
    """Optional public comment reply, to boost engagement (docs §4b)."""

    enabled: bool = False
    message: str = Field(default="", max_length=300)


# The follow prompt has two correct forms, and which one is true depends on
# whether a messaging window is open when the gate blocks.
#
# With an opening DM, tapping the button opened a 24-hour window, so
# `recheck_follow` can poll and deliver the link the moment the follow lands --
# asking for another comment would be asking for nothing.
#
# Without one, the gate blocks at comment time and the single private reply a
# comment allows has just been spent on the prompt itself. There is no window
# and no second reply, so commenting again is genuinely the only way back in.
#
# Both live here so the model default, the worker's fallbacks and the editor
# cannot drift apart -- they did, and the only correct string was the one
# nobody could reach.
FOLLOW_PROMPT_WITH_WINDOW = (
    "Follow me and I'll send the link straight over — no need to comment again."
)
FOLLOW_PROMPT_NO_WINDOW = "Follow me first, then comment again and I'll send the link!"


class FollowGate(BaseModel):
    """Require the commenter to follow before the link is sent.

    Uses the Messaging User Profile API's `is_user_follow_business` — the
    officially supported check. Meta will not answer it until the person has
    messaged the account, so with an opening DM the gate is enforced at the
    postback rather than at the comment (see `workers/tasks.py`).
    """

    enabled: bool = False
    prompt: str = Field(default=FOLLOW_PROMPT_WITH_WINDOW, max_length=300)


class EmailGate(BaseModel):
    """Ask for an email in the DM before sending the payoff."""

    enabled: bool = False
    prompt: str = Field(default="What's your email? I'll send it straight over.", max_length=300)


class AiMatching(BaseModel):
    """Fall back to intent classification when no keyword matched.

    Deliberately opt-in per rule as well as globally: turning the model on
    account-wide should not silently widen every existing rule.
    """

    enabled: bool = False
    intents: list[str] = Field(default_factory=list)
    # None falls back to AI_MIN_CONFIDENCE.
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("intents")
    @classmethod
    def known_intents(cls, values: list[str]) -> list[str]:
        from app.services.ai.classifier import INTENTS

        unknown = [v for v in values if v not in INTENTS]
        if unknown:
            raise ValueError(f"unknown intent(s): {', '.join(unknown)}")
        return values


class Attachment(BaseModel):
    """A file Meta fetches by URL when the payoff is sent.

    A private reply is text-only (docs/meta-api.md §4c), so an attachment can
    only ride the follow-up DM -- which means a rule carrying one must use the
    opening-DM flow to get a messaging window open first.
    """

    type: Literal["image", "video", "audio", "file"]
    url: str = ""
    #  "<uuid>/<filename>" on disk. Kept so the file can be deleted when the
    #  rule goes or the attachment is replaced.
    storage_key: str = ""
    filename: str = ""
    size_bytes: int = 0


class RuleBase(BaseModel):
    name: str = Field(default="", max_length=120)
    trigger: RuleTrigger = "comment"
    scope: RuleScope = "specific"
    instagram_media_ids: list[str] = Field(default_factory=list)
    # Story-reply rules only. Empty means every story, which is the default
    # because a story lives 24 hours -- pinning a rule to one makes it expire
    # with the story, and `expire_with_story` governs what happens then.
    instagram_story_ids: list[str] = Field(default_factory=list)
    # When the last story a rule targets expires, disable the rule rather than
    # leave it enabled and silently unmatchable.
    expire_with_story: bool = True

    keywords: list[str] = Field(default_factory=list)
    any_word: bool = False
    match_mode: MatchMode = "contains"
    case_sensitive: bool = False

    opening_dm: OpeningDm = Field(default_factory=OpeningDm)
    message: str = Field(min_length=1, max_length=1000)
    buttons: list[LinkButton] = Field(default_factory=list)
    attachment: Attachment | None = None

    # Wait this long before replying. Meta's private-reply window is seven days,
    # so a delay capped at 59:59 can never push a send outside it.
    reply_delay_seconds: int = Field(default=0, ge=0, le=3599)

    ai: AiMatching = Field(default_factory=AiMatching)
    public_reply: PublicReply = Field(default_factory=PublicReply)
    require_follow: FollowGate = Field(default_factory=FollowGate)
    collect_email: EmailGate = Field(default_factory=EmailGate)

    # "Only send this DM once per person" — prevents the same commenter being
    # DMed twice by this rule, however many times they comment.
    once_per_person: bool = True
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=10000)
    limits: Limits = Field(default_factory=Limits)

    @field_validator("keywords")
    @classmethod
    def clean_keywords(cls, values: list[str]) -> list[str]:
        # Blank keywords normalise to "" and would match nothing, which is a
        # silent misconfiguration rather than a useful trigger.
        cleaned = [v.strip() for v in values if v and v.strip()]
        if len(cleaned) > MAX_KEYWORDS:
            raise ValueError(f"at most {MAX_KEYWORDS} keywords")
        return cleaned

    @field_validator("buttons")
    @classmethod
    def limit_buttons(cls, values: list[LinkButton]) -> list[LinkButton]:
        if len(values) > MAX_BUTTONS:
            raise ValueError(f"at most {MAX_BUTTONS} buttons")
        return values


class RuleCreate(RuleBase):
    @field_validator("instagram_media_ids")
    @classmethod
    def specific_needs_posts(cls, values: list[str]) -> list[str]:
        return values


class RuleUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    trigger: RuleTrigger | None = None
    scope: RuleScope | None = None
    instagram_media_ids: list[str] | None = None
    instagram_story_ids: list[str] | None = None
    expire_with_story: bool | None = None
    keywords: list[str] | None = None
    any_word: bool | None = None
    match_mode: MatchMode | None = None
    case_sensitive: bool | None = None
    opening_dm: OpeningDm | None = None
    message: str | None = Field(default=None, min_length=1, max_length=1000)
    buttons: list[LinkButton] | None = None
    attachment: Attachment | None = None
    reply_delay_seconds: int | None = Field(default=None, ge=0, le=3599)
    ai: AiMatching | None = None
    public_reply: PublicReply | None = None
    require_follow: FollowGate | None = None
    collect_email: EmailGate | None = None
    once_per_person: bool | None = None
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=10000)
    limits: Limits | None = None


class MessagePreviewRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    username: str = "john123"
    comment: str = "LINK please!"
    post_caption: str = "5 AI tools every developer should know"


class PostPublic(BaseModel):
    """A synced Instagram post, with how many rules are armed on it."""

    id: str
    instagram_media_id: str
    media_type: str | None = None
    media_product_type: str | None = None
    caption: str | None = None
    permalink: str | None = None
    # `thumbnail_url` is video-only; images and carousels use `media_url`.
    thumbnail_url: str | None = None
    media_url: str | None = None
    published_at: datetime | None = None
    like_count: int | None = None
    comments_count: int | None = None
    rule_count: int = 0

    @classmethod
    def from_document(cls, document: dict, rule_count: int = 0) -> "PostPublic":
        return cls(
            id=str(document["_id"]),
            instagram_media_id=str(document.get("instagram_media_id", "")),
            media_type=document.get("media_type"),
            media_product_type=document.get("media_product_type"),
            caption=document.get("caption"),
            permalink=document.get("permalink"),
            thumbnail_url=document.get("thumbnail_url"),
            media_url=document.get("media_url"),
            published_at=document.get("published_at"),
            like_count=document.get("like_count"),
            comments_count=document.get("comments_count"),
            rule_count=rule_count,
        )


class StoryPublic(BaseModel):
    """An active story, for the per-story picker.

    `permalink` and `published_at` are optional because Meta omits them for
    video stories (docs/meta-api.md §4d) -- `first_seen_at` is the only
    ordering key every story has.
    """

    id: str
    instagram_story_id: str
    media_type: str | None = None
    caption: str | None = None
    permalink: str | None = None
    thumbnail_url: str | None = None
    media_url: str | None = None
    published_at: datetime | None = None
    first_seen_at: datetime | None = None
    rule_count: int = 0

    @classmethod
    def from_document(cls, document: dict, rule_count: int = 0) -> "StoryPublic":
        return cls(
            id=str(document["_id"]),
            instagram_story_id=str(document.get("instagram_story_id", "")),
            media_type=document.get("media_type"),
            caption=document.get("caption"),
            permalink=document.get("permalink"),
            thumbnail_url=document.get("thumbnail_url"),
            media_url=document.get("media_url"),
            published_at=document.get("published_at"),
            first_seen_at=document.get("first_seen_at"),
            rule_count=rule_count,
        )


class RuleCloneRequest(BaseModel):
    """Which posts to copy an automation onto.

    The source is a *rule* id in the path, not a post id: a post may hold more
    than one automation, so "clone this post's automation" is ambiguous and the
    caller has to say which one.
    """

    instagram_media_ids: list[str] = Field(min_length=1, max_length=MAX_CLONE_TARGETS)
