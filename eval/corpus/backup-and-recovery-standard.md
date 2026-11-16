# Backup and Recovery Standard

**Owner:** IT Operations  |  **Version:** 1.3

## 1. Backup schedule
- Production databases are backed up every 4 hours.
- File shares are backed up nightly.
- Backup copies are stored in a second region.

## 2. Recovery objectives

| Tier | Example systems | Recovery time objective | Recovery point objective |
|---|---|---|---|
| Tier 1 | Customer platform, payments | 4 hours | 1 hour |
| Tier 2 | Finance system, HR portal | 24 hours | 4 hours |
| Tier 3 | Intranet, reporting | 72 hours | 24 hours |

## 3. Testing
- A restore test is performed every quarter for each Tier 1 system.
- Results of restore tests are recorded in the operations register.
