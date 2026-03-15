"""SQLAlchemy ORM models for the Ad Intelligence platform."""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


class Brand(Base):
    __tablename__ = "brands"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    page_id = Column(Text, unique=True, nullable=False)
    page_name = Column(Text, nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=True)
    ad_count = Column(BigInteger, default=0, server_default=text("0"))
    created_at = Column(DateTime(timezone=True), server_default=text("NOW()"))

    # Relationships
    ads = relationship("Ad", back_populates="brand", cascade="all, delete-orphan")


class Ad(Base):
    __tablename__ = "ads"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    ad_archive_id = Column(Text, unique=True, nullable=False)
    brand_id = Column(UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=True)
    page_name = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False)
    ad_type = Column(Text, nullable=True)
    classification_method = Column(Text, nullable=True)
    caption = Column(Text, nullable=True)
    link_title = Column(Text, nullable=True)
    link_description = Column(Text, nullable=True)
    cta_type = Column(Text, nullable=True)
    publisher_platforms = Column(ARRAY(Text), nullable=True)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)

    # Impression range fields
    impressions_lower = Column(BigInteger, nullable=True)
    impressions_upper = Column(BigInteger, nullable=True)
    impressions_mid = Column(
        BigInteger,
        Computed(
            "CASE WHEN impressions_lower IS NOT NULL AND impressions_upper IS NOT NULL "
            "THEN (impressions_lower + impressions_upper) / 2 ELSE NULL END",
            persisted=True,
        ),
    )

    # Reach range fields
    reach_lower = Column(BigInteger, nullable=True)
    reach_upper = Column(BigInteger, nullable=True)
    reach_mid = Column(
        BigInteger,
        Computed(
            "CASE WHEN reach_lower IS NOT NULL AND reach_upper IS NOT NULL "
            "THEN (reach_lower + reach_upper) / 2 ELSE NULL END",
            persisted=True,
        ),
    )

    # Spend range fields
    spend_lower = Column(Numeric, nullable=True)
    spend_upper = Column(Numeric, nullable=True)

    # Media
    snapshot_url = Column(Text, nullable=True)
    media_local_path = Column(Text, nullable=True)
    frame_paths = Column(ARRAY(Text), nullable=True)
    frame_metadata = Column(JSONB, nullable=True)

    # Performance scoring
    performance_score = Column(Numeric(5, 4), nullable=True)
    performance_label = Column(Text, nullable=True)
    performance_percentile = Column(Numeric(5, 2), nullable=True)

    # Raw API data
    raw_meta_json = Column(JSONB, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), server_default=text("NOW()"), onupdate=datetime.utcnow)

    # Constraints
    __table_args__ = (
        CheckConstraint("ad_type IN ('STATIC', 'VIDEO', 'UNKNOWN')", name="ck_ads_ad_type"),
        CheckConstraint("performance_label IN ('STRONG', 'AVERAGE', 'WEAK')", name="ck_ads_performance_label"),
        Index("idx_ads_brand_id", "brand_id"),
        Index("idx_ads_is_active", "is_active"),
        Index("idx_ads_ad_type", "ad_type"),
        Index("idx_ads_performance_label", "performance_label"),
    )

    # Relationships
    brand = relationship("Brand", back_populates="ads")
    insight = relationship("Insight", back_populates="ad", uselist=False, cascade="all, delete-orphan")


class Insight(Base):
    __tablename__ = "insights"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    ad_id = Column(UUID(as_uuid=True), ForeignKey("ads.id", ondelete="CASCADE"), unique=True, nullable=False)
    summary = Column(Text, nullable=False)
    factors = Column(JSONB, nullable=False)
    model_used = Column(Text, nullable=False)
    prompt_version = Column(Text, nullable=False, server_default=text("'v1'"))
    analysis_mode = Column(Text, nullable=False, server_default=text("'visual'"))
    ad_context = Column(Text, nullable=False, server_default=text("'commercial'"))
    generated_at = Column(DateTime(timezone=True), server_default=text("NOW()"))

    ad = relationship("Ad", back_populates="insight")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    job_type = Column(Text, nullable=False)
    status = Column(Text, nullable=False, server_default=text("'PENDING'"))
    payload = Column(JSONB, nullable=False)
    result = Column(JSONB, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), server_default=text("NOW()"), onupdate=datetime.utcnow)
