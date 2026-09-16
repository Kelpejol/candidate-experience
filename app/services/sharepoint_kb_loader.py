"""Load a campaign's (or the general) KB straight from SharePoint.

Lists the .docx files in one folder of the shared SharePoint site, downloads
each, and converts it through docx_kb_loader.docx_to_markdown — producing
exactly the (source_name, text) pairs voice_kb_ingest.reindex_campaign already
consumes from the local-directory loader. Only the *loading* changes here;
chunking/embedding/indexing are the same code path either way.
"""

from app.integrations.sharepoint_client import SharePointClient
from app.services.docx_kb_loader import docx_to_markdown


def load_sharepoint_kb_folder(
    client: SharePointClient,
    hostname: str,
    site_path: str,
    folder_path: str,
    library_name: str | None = None,
) -> tuple[list[tuple[str, str]], list[str]]:
    """Fetch every .docx in one SharePoint folder, converted to markdown text.

    `library_name` names the document library the folder lives in (e.g.
    "Candidate experience KB"), when it isn't the site's default library.
    Omit it for a site with only the one, default library.

    Returns (documents, warnings): `documents` is the same [(source_name,
    text), ...] shape load_local_kb_dir returns, so it drops straight into
    reindex_campaign unchanged. `warnings` collects every reason a file (or a
    heading within a file) was skipped, from every file — one bad file (wrong
    format, malformed structure) doesn't block indexing the rest of a folder.
    """
    site = client.get_site(hostname, site_path)
    site_id = site["id"]

    drive_id = client.get_drive_id(site_id, library_name) if library_name else None
    drive_kwargs = {"drive_id": drive_id} if drive_id else {}

    items = client.list_drive_items(site_id, folder_path, **drive_kwargs).get("value", [])

    documents: list[tuple[str, str]] = []
    warnings: list[str] = []

    for item in items:
        name = item.get("name", "")

        if "folder" in item:
            continue  # a sub-folder — this level only indexes files directly in it

        if not name.lower().endswith(".docx"):
            warnings.append(f"Skipped {name!r} — only .docx files are indexed.")
            continue

        data = client.download_file(site_id, item["id"], **drive_kwargs)
        try:
            text, file_warnings = docx_to_markdown(data)
        except ValueError as exc:
            warnings.append(f"Skipped {name!r} — {exc}")
            continue

        warnings.extend(f"{name}: {w}" for w in file_warnings)

        if text.strip():
            documents.append((name, text))
        else:
            warnings.append(f"Skipped {name!r} — no usable questions found in it.")

    return documents, warnings
