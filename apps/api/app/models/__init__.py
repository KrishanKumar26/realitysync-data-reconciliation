"""ORM models.

Intentionally empty in Phase 1. The 21-table domain schema (organizations,
users, memberships, sessions, data_sources, source_credentials,
source_schemas, source_streams, entities, entity_mappings, observations,
events, reality_states, reality_state_versions, state_snapshots, conflicts,
conflict_evidence, investigations, policies, audit_logs, sync_runs) is
implemented across Phases 2-5.

Every model must import Base from app.db.base so it is registered on the
shared metadata that Alembic autogenerate inspects.
"""
