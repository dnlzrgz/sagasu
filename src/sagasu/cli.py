import asyncio
from pathlib import Path
from collections import Counter
import click
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from rich.console import Console
from sagasu.db import init_db
from sagasu.files import list_files, extract_tokens
from sagasu.models import Doc, Posting, Word
from sagasu.repositories import DocRepository, PostingRepository, WordRepository
from sagasu.services import SearchService
from sagasu.settings import Settings
from sagasu.utils import tokenize

console = Console()


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx) -> None:
    """
    A personal search engine.
    """

    ctx.ensure_object(dict)

    # Load settings
    settings = Settings()
    ctx.obj["settings"] = settings

    # Setup SQLite database
    engine = create_engine(settings.sqlite_url)
    init_db(engine)

    ctx.obj["engine"] = engine

    # If no subcommand, exit
    if ctx.invoked_subcommand is None:
        print(ctx.obj["settings"])


async def _index_files(session: Session, directories: list[Path]) -> None:
    doc_repo = DocRepository(session)
    posting_repo = PostingRepository(session)
    word_repo = WordRepository(session)

    sempahore = asyncio.Semaphore(10)

    async def process_file(file: Path):
        async with sempahore:
            uri = f"{file.resolve()}"
            try:
                result = await extract_tokens(file)
                doc_in_db = doc_repo.get_by_attributes(uri=uri)
                if doc_in_db:
                    return

                doc_in_db = doc_repo.add(Doc(uri=uri))

                tokens = Counter(result)
                for token, count in tokens.items():
                    word_in_db = word_repo.get_by_attributes(word=token)
                    if not word_in_db:
                        word_in_db = word_repo.add(Word(word=token))

                    posting_in_db = posting_repo.get_by_attributes(
                        word_id=word_in_db.id,
                        doc_id=doc_in_db.id,
                    )

                    if not posting_in_db:
                        posting_in_db = posting_repo.add(
                            Posting(
                                word=word_in_db,
                                doc=doc_in_db,
                                count=count,
                            )
                        )

                session.commit()
            except Exception as exc:
                console.print(f"[bold red]ERROR:[/] While reading {file}: {exc}")

    with console.status(
        "Indexing documents... This may take a moment", spinner="earth"
    ):
        for directory in directories:
            try:
                files = list_files(directory)
            except Exception as exc:
                console.print(f"[bold red]ERROR:[/] {exc}")
                continue

            tasks = [process_file(file) for file in files]
            await asyncio.gather(*tasks)


@cli.command(name="index", help="")
@click.pass_context
def index(ctx) -> None:
    engine = ctx.obj["engine"]
    settings = ctx.obj["settings"]

    with Session(engine) as session:
        asyncio.run(
            _index_files(
                session, [settings.directories[dir] for dir in settings.directories]
            )
        )


@cli.command(name="search", help="")
@click.option("--count", default=10, help="Number of results.")
@click.option("--query", prompt="Search query", help="")
@click.pass_context
def search(ctx, count: int, query: str) -> None:
    engine = ctx.obj["engine"]

    tokens = tokenize(query)

    with Session(engine) as session:
        search_service = SearchService(session)
        results = search_service.search(tokens)
        if not results:
            return

        for result in results:
            print(result)
