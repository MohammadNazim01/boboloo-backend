import uuid
import enum
from datetime import datetime
from sqlalchemy import (
    Column,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Float,
    Text,
    Index,
    Date,
    UniqueConstraint,
    Integer,
    Enum,
    func,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database.database import Base


# =========================
# UUID PRIMARY KEY HELPER
# =========================
def UUID_PK():
    return Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


# =========================
# PARENT
# =========================
class Parent(Base):
    __tablename__ = "parents"

    id = UUID_PK()
    firebase_uid = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True)
    name = Column(String, index=True)
    is_active = Column(Boolean, default=True)

    children = relationship(
        "Child",
        back_populates="parent",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    toys = relationship(
        "Toy",
        back_populates="owner",
        lazy="selectin",
    )


# =========================
# CHILD
# =========================
class Child(Base):
    __tablename__ = "children"

    id = UUID_PK()

    parent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("parents.id"),
        nullable=False,
        unique=True,
        index=True,
    )

    name = Column(String, nullable=False)
    age = Column(Integer, nullable=False)
    birth_date = Column(Date, nullable=True, index=True)
    guardian_name = Column(String)

    interests = Column(JSONB, nullable=False, default=lambda: [])
    keywords_filter = Column(JSONB, nullable=False, default=lambda: [])
    focus_topics = Column(JSONB, nullable=False, default=lambda: [])

    onboarding_completed = Column(Boolean, default=False)

    is_deleted = Column(Boolean, default=False, index=True)
    deleted_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)

    parent = relationship("Parent", back_populates="children")

    conversations = relationship(
        "Conversation",
        back_populates="child"
    )

    analytics = relationship(
        "ChildAnalytics",
        back_populates="child",
        uselist=False,
    )

    analytics_history = relationship(
        "AnalyticsHistory",
        back_populates="child",
        lazy="selectin",
    )


# =========================
# TOY STATUS ENUM
# =========================
class ToyStatus(str, enum.Enum):
    PROVISIONED = "PROVISIONED"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"

# =========================
# MESSAGE ROLE ENUM
class MessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"

# =========================
# TOY
# =========================
class Toy(Base):
    __tablename__ = "toys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    toy_uuid = Column(
        UUID(as_uuid=True),
        unique=True,
        nullable=False,
        default=uuid.uuid4,
        index=True,
    )

    factory_device_id = Column(
        String,
        unique=True,
        nullable=False,
        index=True,
    )

    owner_parent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("parents.id"),
        nullable=True,
        index=True,
    )

    active_child_id = Column(
        UUID(as_uuid=True),
        ForeignKey("children.id"),
        nullable=True,
        index=True,
    )

    owner = relationship(
        "Parent",
        back_populates="toys"
    )

    active_child = relationship("Child")

    api_keys = relationship(
        "APIKey",
        backref="toy",
        lazy="selectin"
    )

    status = Column(Enum(ToyStatus), default=ToyStatus.PROVISIONED)
    is_active = Column(Boolean, default=True)

    claimed_at = Column(DateTime(timezone=True), nullable=True)
    last_seen = Column(DateTime(timezone=True), nullable=True)

    manufactured_at = Column(DateTime(timezone=True), nullable=True)

    firmware_version = Column(String, nullable=True)

    factory_batch = Column(String, index=True, nullable=True)

    hardware_revision = Column(String, nullable=True)

    battery_level = Column(Integer, nullable=True)

    wifi_signal = Column(Integer, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        index=True,
    )

    updated_at = Column(
        DateTime(timezone=True),
        onupdate=datetime.utcnow,
    )


# =========================
# API KEY
# =========================
class APIKey(Base):
    __tablename__ = "api_keys"

    id = UUID_PK()

    key_hash = Column(String, unique=True, index=True, nullable=False)

    toy_id = Column(
        UUID(as_uuid=True),
        ForeignKey("toys.id"),
        index=True,
        nullable=False,
    )

    revoked = Column(Boolean, default=False)


Index(
    "idx_api_key_hash_revoked",
    APIKey.key_hash,
    APIKey.revoked,
)


# =========================
# CONVERSATION
# =========================
class Conversation(Base):
    __tablename__ = "conversations"

    __table_args__ = (
        UniqueConstraint(
            "child_id",
            "conversation_date",
            name="uq_child_daily_conversation",
        ),
    )

    id = UUID_PK()

    child_id = Column(
        UUID(as_uuid=True),
        ForeignKey("children.id"),
        index=True,
        nullable=False,
    )

    conversation_date = Column(Date, index=True, nullable=False)

    started_at = Column(DateTime(timezone=True))
    last_activity = Column(DateTime(timezone=True))

    child = relationship(
        "Child",
        back_populates="conversations"
    )

    messages = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
    )


Index(
    "idx_conversation_child_date",
    Conversation.child_id,
    Conversation.conversation_date,
)


# =========================
# MESSAGE
# =========================
class Message(Base):
    __tablename__ = "messages"

    id = UUID_PK()

    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id"),
        index=True,
        nullable=False,
    )

    role = Column(Enum(MessageRole), nullable=False, index=True)
    content = Column(Text, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    conversation = relationship(
        "Conversation",
        back_populates="messages"
    )


Index(
    "idx_conversation_created",
    Message.conversation_id,
    Message.created_at,
)


# =========================
# CHILD ANALYTICS
# =========================
class ChildAnalytics(Base):
    __tablename__ = "child_analytics"

    id = UUID_PK()

    child_id = Column(
        UUID(as_uuid=True),
        ForeignKey("children.id"),
        unique=True,
        nullable=False,
        index=True,
    )

    fq = Column(Float, nullable=False)
    vq = Column(Float, nullable=False)
    cq = Column(Float, nullable=False)
    mq = Column(Float, nullable=False)
    gq = Column(Float, nullable=False)

    velocity = Column(String)
    confidence = Column(Float)
    trend_percent = Column(Float)

    breakdown_json = Column(JSONB)
    insight_json = Column(JSONB)

    algorithm_version = Column(String)

    updated_at = Column(
        DateTime,
        nullable=False,
        index=True,
    )

    child = relationship(
        "Child",
        back_populates="analytics"
    )


# =========================
# ANALYTICS HISTORY
# =========================
class AnalyticsHistory(Base):
    __tablename__ = "analytics_history"

    __table_args__ = (
        UniqueConstraint(
            "child_id",
            "analytics_date",
            name="uq_child_daily_analytics"
        ),
    )

    id = UUID_PK()

    child_id = Column(
        UUID(as_uuid=True),
        ForeignKey("children.id"),
        nullable=False,
        index=True,
    )

    analytics_date = Column(Date, index=True, nullable=False)

    fq = Column(Float)
    vq = Column(Float)
    cq = Column(Float)
    mq = Column(Float)
    gq = Column(Float)

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        index=True,
    )

    child = relationship(
        "Child",
        back_populates="analytics_history"
    )


Index(
    "idx_analytics_child_date",
    AnalyticsHistory.child_id,
    AnalyticsHistory.created_at,
)


# =========================
# GIN INDEXES
# =========================
Index(
    "idx_children_interests_gin",
    Child.interests,
    postgresql_using="gin",
)

Index(
    "idx_children_keywords_gin",
    Child.keywords_filter,
    postgresql_using="gin",
)


# =========================
# AUDIT LOG
# =========================
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = UUID_PK()

    parent_id = Column(UUID(as_uuid=True), index=True)
    child_id = Column(UUID(as_uuid=True), index=True)

    action = Column(String, nullable=False)
    event_data = Column(JSONB)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# =========================
# INTERACTION SETTINGS
# =========================
class InteractionSettings(Base):
    __tablename__ = "interaction_settings"

    id = UUID_PK()

    child_id = Column(
        UUID(as_uuid=True),
        ForeignKey("children.id"),
        unique=True,
        nullable=False,
        index=True,
    )

    smart_adapt_mode = Column(Boolean, default=True)
    custom_tune = Column(Boolean, default=False)

    word_complexity = Column(Integer, default=3)
    speech_speed = Column(Integer, default=2)

    new_words_per_session = Column(Integer, default=3)

    question_frequency = Column(String, default="balanced")

    topic_focus = Column(Integer, default=3)

    command_steps = Column(Integer, default=2)

    patience_level = Column(Integer, default=3)

    created_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        index=True,
    )

    updated_at = Column(
        DateTime(timezone=True),
        onupdate=datetime.utcnow,
    )

    child = relationship("Child")

# =========================
# CHILD VOCABULARY MEMORY
# =========================
class ChildVocabularyMemory(Base):
    __tablename__ = "child_vocabulary_memory"

    id = UUID_PK()

    child_id = Column(
        UUID(as_uuid=True),
        ForeignKey("children.id"),
        nullable=False,
        index=True,
    )

    word = Column(String(64), nullable=False)

    first_seen = Column(Date, nullable=False, index=True)
    last_seen = Column(Date, nullable=False, index=True)

    usage_count = Column(Integer, default=1, nullable=False)

    child = relationship("Child", lazy="selectin")

    __table_args__ = (
        UniqueConstraint(
            "child_id",
            "word",
            name="uq_child_word_memory",
        ),
    )


Index(
    "idx_child_vocab_child_word_lower",
    ChildVocabularyMemory.child_id,
    func.lower(ChildVocabularyMemory.word),
)

Index(
    "idx_vocab_child_first_seen",
    ChildVocabularyMemory.child_id,
    ChildVocabularyMemory.first_seen,
)