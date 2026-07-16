<!-- Kortex context — auto-generated, do not edit. Add .kortex/ to .gitignore -->
# Kortex Project Intelligence
_Generated: 2026-07-16T16:23:10.307Z_

## Affirmed Decisions

### Decision: changes to function:B
The function B appears to be a refactoring effort aimed at improving the readability and maintainability of the codebase.
_Tags: signal:git, kind:decision_
_Symbol: `function:B`_
_Confidence: 30%_

### Sanitize file segment
The function trims, replaces special characters, and limits length to ensure safe file names.
_Tags: file handling, security, auto-analysed_
_Symbol: `safeFileSegment`_
_Confidence: 35%_

### Decision: changes to function:safeFileSegment
The addition of 'safeFileSegment' suggests improved file handling and security measures.
_Tags: signal:git, kind:decision_
_Symbol: `function:safeFileSegment`_
_Confidence: 30%_

### URL modification for Dropbox direct download
The function modifies URLs to enable direct downloads from Dropbox by appending '?dl=1'. This is done to streamline the process for users accessing reports.
_Tags: security, usability, auto-analysed_
_Symbol: `toDirectDownloadUrl`_
_Confidence: 35%_

### Fallback on Content-Type
The function prioritizes guessing file extensions from URLs but falls back to using Content-Type headers when URL parsing fails. This ensures compatibility with various file types even if URLs are malformed.
_Tags: file extension guessing, Content-Type handling, auto-analysed_
_Symbol: `guessExtension`_
_Confidence: 35%_

### Fallback on Content-Type
The function prioritizes file extension extraction from URL over Content-Type for determining report types, providing flexibility but potentially leading to inconsistencies.
_Tags: url-parsing, content-type, report-type, auto-analysed_
_Symbol: `pathname`_
_Confidence: 35%_

### Transaction for Version Creation
The function uses a transaction to ensure that creating an active criteria version is atomic.
_Tags: database, consistency, auto-analysed_
_Symbol: `listCriteriaVersions`_
_Confidence: 35%_

### Version Activation Management
The function manages activation of ranking criteria versions for a given vacancy, ensuring only one active version exists at any time.
_Tags: prisma, transactions, data consistency, auto-analysed_
_Symbol: `getActiveCriteriaVersion`_
_Confidence: 35%_

### Transaction for Criteria Version Creation
The function uses a transaction to ensure that all related updates and creations are performed atomically, maintaining data integrity.
_Tags: data_integrity, atomic_operations, auto-analysed_
_Symbol: `createCriteriaVersion`_
_Confidence: 35%_

### Version Activation Transaction
The function activates a criteria version for a vacancy in a transaction to ensure data consistency.
_Tags: prisma, transaction, data integrity, auto-analysed_
_Symbol: `nextVersion`_
_Confidence: 35%_
