# Terminology

RealitySync was built with the vocabulary of the people who built it. That
vocabulary is precise, and it is also wrong for the person reading the screen:
"entity", "observation", "stream" and "attribute" are all words that require the
data model to be explained before the interface makes sense.

This pass renamed what a person reads. It renamed nothing a machine reads.

---

## The rule that decided every case

A name was changed only if it is **displayed**. A name was kept if anything
depends on its exact spelling — a URL, a JSON key, a column, a type, a variable.

That split is not cosmetic caution. Renaming a JSON field breaks every client
that already parses it; renaming a column needs a migration and rewrites data;
renaming a route breaks bookmarks and links that are already in circulation. The
gain would have been a tidier codebase, which nobody using the product ever
sees. So the internal names stayed exactly as they were, and the labels above
them changed.

Where the two now differ, the internal name is the one in the code and the
friendly name is the one in the interface. `entity_type` is still `entity_type`
in the request body; the field above the box says **Type**.

---

## Mapping

### Concepts

| Old | New | Why |
| --- | --- | --- |
| Entity | **Item** | "One real thing your sources describe" — a product, a shipment. "Entity" is a modelling term. |
| Observation | **Record** | What a source stated, at a time. "Observation" reads as scientific instrumentation. |
| Stream | **Table** | It *is* a table in the source database. The extra word bought nothing. |
| Attribute | **Field** | The word people already use for a column of information. |
| Reality state | **Current value** | What RealitySync currently believes is true for one field. |
| Natural key | **Reference ID** | How *you* refer to the item, e.g. `LAPTOP-13`. |
| Mapping / to map | **Link** | Binding a source row to an item. |
| Ingestion | **Data received** | |
| Schema (the list) | **Available tables** | |
| Event-time column | **Date column** | The distinction between "when it was true" and "when it was written" is still asked, in those words. |

### Navigation

| Old | New |
| --- | --- |
| Reality | **Current State** |

The route stays `/reality`. The label is the part a person reads; the URL is the
part their bookmark depends on.

### Actions

| Old | New |
| --- | --- |
| Discover schema | **Find tables** |
| Re-read schema | **Refresh list** |
| Configure stream | **Add table** |
| Configured (badge) | **Added** |
| New entity / Create entity | **New item** / **Create item** |
| Map a source row / Map row | **Link a table row** / **Link row** |
| Source rows for X | **Linked data for X** |

### Statistics

| Old | New |
| --- | --- |
| Reality confidence | **Confidence** |
| Scored states | **Scored fields** |
| Observations | **Records** |
| Entities | **Items** |
| Streams | **Tables** |
| Time axis | **View by** |

Dropping "Reality" from the confidence panel collided with the stat inside it,
which was also called "Confidence". The stat is now **Average**, matching what
the same number is called when scoring is available. Two identical headings on
one page would have been a worse result than the jargon.

### API messages

Nine `detail` strings. Status codes, routes, and response shapes are untouched.

| Old | New |
| --- | --- |
| Entity not found. | Item not found. |
| Stream not found. | Table not found. |
| An entity with that type and key already exists in this workspace. | An item with that type and reference ID already exists in this workspace. |
| That table is already configured as a stream. | That table has already been added. |
| That source row is already mapped to an entity. | That source row is already linked to an item. |
| No reality state for that attribute. | No current value for that field. |
| No observations state that attribute for this entity. | No source has stated that field for this item. |
| This attribute is fully scored; read it from the reality endpoint. | This field is fully scored; read it from the current-state endpoint. |

---

## Deliberately not renamed

| Kept | Where | Renaming it would |
| --- | --- | --- |
| `/api/entities`, `/api/reality`, `/api/streams` | routes | break every existing client and link |
| `entity_type`, `natural_key`, `observation_count`, `stream_id`, `external_id` | JSON fields | break every client that parses a response |
| `entities`, `observations`, `source_streams`, `reality_states` | database tables and columns | require a data migration for a cosmetic gain |
| `Entity`, `Observation`, `SourceStream`, `RealityState` | Python and TypeScript types | churn every file for no user-visible change |
| `EntityError`, `DuplicateMappingError`, `StreamNotFoundError` | internal exceptions | nothing; they are never displayed |
| `reality`, `entity`, `observation` in comments and docstrings | source | detach the code from the schema it describes |

Product names that are not jargon were also left alone: **Sources**,
**Conflicts**, **Timeline**, **Overview**, **Settings**, **Sync**, and
**Confidence** are already the plainest available words.

---

## Verification

No functional change was intended and none was made. What ran afterwards:

| Gate | Result |
| --- | --- |
| `pytest` | 544 passed, 1 skipped |
| `ruff check` / `ruff format --check` | clean |
| `mypy --strict` | clean, 92 source files |
| frontend tests | 110 passed |
| `tsc --noEmit` | clean |
| `eslint` | clean |
| `next build` | compiled successfully |

Frontend test assertions were updated where they asserted on renamed display
text — that is the tests correctly tracking a deliberate label change, not tests
being loosened to pass. No assertion was removed and no test was skipped.

Alembic migrations, ORM models and Pydantic schemas have **zero** diff. The
backend diff is nine string literals in two route files.
