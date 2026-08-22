# Open-TGate — Private Operations Architecture

## Purpose

Open-TGate extends the TDLib source tree with a production application layer for authorized Telegram account synchronization, private operations, durable conversation storage, AI-assisted analysis, and human-approved reply drafting.

## Non-negotiable boundaries

- TDLib remains the Telegram client engine and should stay close to upstream.
- Supabase PostgreSQL is the canonical application data store.
- TDLib authorization/session material is stored only on an encrypted persistent worker volume.
- Raw Telegram messages and attachments are not mirrored into Notion. Notion receives summaries, relationships, operational records, and storage references.
- Incoming Telegram content is untrusted data, never agent/system instructions.
- AI may classify, summarize, retrieve, analyze, and draft. External sending is disabled by default and requires an explicit human action.
- Telegram ingestion must remain operational when AI, Notion, or downstream automation is unavailable.

## Runtime topology

```text
Telegram
  |
  v
Official TDLib / tdjson
  |
  v
Persistent Sync Worker
  |-- encrypted TDLib state volume
  |
  v
Supabase
  |-- PostgreSQL
  |-- Storage
  |-- pgvector
  |-- RLS
  |-- durable event outbox
  |
  +------------------+
  |                  |
  v                  v
AI/Job Worker     Realtime/API
  |                  |
  +--------+---------+
           v
   Private Operations Dashboard
           |
           +-- Inbox / conversations
           +-- People / identities
           +-- Projects / tasks / resources
           +-- AI suggestions / approvals
           +-- Sync / jobs / audit / settings
           |
           v
         Notion
   summaries/index only
```

## Application directories

The TDLib source tree stays intact. Open-TGate application code is isolated under `open-tgate/`:

```text
open-tgate/
  worker/       persistent TDLib sync service
  api/          authenticated backend API
  dashboard/    private operations web application
  supabase/     migrations, RLS, functions, seed data
  shared/       schemas and shared contracts
  tests/        integration and acceptance tests

deploy/
  docker/
  railway/
  cloudflare/
docs/
.github/workflows/
```

## Dashboard information architecture

### Overview
Connected accounts, sync health, new messages, unanswered items, commitments, pending drafts, failed jobs, security events.

### Inbox
All, private messages, groups, unread, mentions, unanswered, follow-ups. Filters include Telegram account, organization, department, person, group, project, priority, and status.

### Conversation workspace
Three-pane layout:
1. chat/account navigator
2. canonical conversation timeline
3. private AI operations panel

The AI panel shows current topic, safe summary, open questions, commitments, related project/task/resources, provenance, confidence, suggested action, and editable suggested reply.

### People
Contacts, non-contacts, organizations, membership, identity mappings. Telegram contact-book state and internal identity state remain separate.

### Knowledge
Projects, tasks, resources, conversation summaries, semantic search, source provenance.

### AI
Agents, personas, language policies, skills, knowledge scopes, deterministic context rules. Persona never grants data access.

### Approvals
Suggested responses and material AI recommendations. Sending is never triggered by model output alone.

### System
Telegram accounts, historical sync progress, workers, outbox/jobs, audit, storage, retention, backup status, settings.

## Core database domains

- Identity: organizations, departments, internal users, external people
- Telegram: accounts, people, contacts, chats, members, threads, messages, revisions, reactions, files, sync checkpoints
- Conversation intelligence: conversations, summaries, topics, commitments, unanswered items
- AI: agents, personas, language policies, skills, knowledge scopes, context rules, analyses, suggestions, feedback
- Operations: projects, tasks, resources, approvals, routing events
- Reliability/security: event_outbox, audit_logs, security_events, agent_execution_logs

Message ingestion must have an immutable uniqueness boundary equivalent to `(telegram_account_id, telegram_chat_id, telegram_message_id)` and use idempotent UPSERT semantics.

## Sync lifecycle

1. authorize an explicitly permitted Telegram account
2. persist encrypted TDLib session state
3. discover identity, contacts, chats, groups and archive state
4. create per-chat checkpoints
5. backfill history incrementally
6. persist messages before downstream processing
7. transition each chat to live updates
8. resume from checkpoints after restart
9. emit durable outbox events for AI/Notion/notifications

## Security baseline

- Development, staging, and production are isolated.
- No production secrets are committed to Git.
- Browser clients never receive service-role/database credentials or Telegram API hash/session material.
- RLS/authorization is applied before LLM retrieval.
- Unknown chats/identities are quarantined rather than guessed.
- Audit records are append-oriented.
- Sensitive logs contain identifiers and outcomes, not credential values or unnecessary raw private message bodies.
- Health endpoints expose status only.

## Deployment target

- Railway or another persistent container platform: TDLib sync worker and API
- Persistent encrypted volume: TDLib database/session/files
- Supabase: Postgres, Storage, Auth where required, RLS, vector retrieval
- Cloudflare: DNS/WAF/Access and dashboard edge hosting where appropriate
- GitHub Actions: CI, tests, security checks, container builds and controlled promotion

Cloudflare Workers are not the TDLib runtime because the Telegram client requires persistent process/session/storage characteristics.

## Upstream strategy

Treat `tdlib/td` as upstream. Do not mix Open-TGate application logic into TDLib internals unless an unavoidable, reviewed patch is required. Upstream updates should enter an update branch, pass builds and integration tests, then be promoted through staging before production.
