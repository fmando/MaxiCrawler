# Scripts

Tools for looking after a library that already exists. They are not part of the
application: nothing in `src/` imports them, and removing this directory would
change nothing about what MaxiCrawler does.

They exist because a running installation collects questions the interface does
not ask. The library page reports damage on *one* entry at a time; it never says
"forty-one of your records point at files that are gone". A crawl of an image
directory leaves behind sprites and icons that were fine to fetch and are not
worth keeping. Those are jobs for a pass over the whole shelf, run once,
watched, and then forgotten about again.

## Running them

Each script takes `--config` and reads the same settings file the server does —
that is where it learns the library path. Without it, the defaults apply.

```bash
./.venv/Scripts/python.exe scripts/prune_small_payloads.py --config settings.toml
```

They add `src/` to the import path themselves, so a checkout is enough; nothing
has to be installed.

## Two rules they all keep

**Nothing is written without `--apply`**, and a script that cannot write does
not offer the flag at all. Both halves are tested: a destructive script without
it would act unasked, and a reporting script that offers it suggests it might do
something. Where the flag exists, the default run prints what it *would* do and
stops — a pass over a real library is a list worth reading before it is a thing
worth doing, and the count at the bottom is usually the first surprise.

**They go through `LibraryService`, not through the filesystem.** Removing a
file with `rm` leaves the record behind claiming a payload that is not there —
which the entry's page reports as damage, because it cannot tell that apart from
a file somebody moved — and the next "queue every match" fetches it again, since
"the library holds this" is answered by the record *and* the file. Every
destructive step here therefore goes through the same call the buttons in the
interface use, which writes the headstone in the same breath (ADR-041).

## Why these are not CLI commands

`maxicrawler library doctor` would read better. It would also be a public
interface with a promise of backwards compatibility attached, for a job that is
done by whoever administers the machine, a handful of times, from a shell they
are already sitting in. These are the sharp tools in the drawer, not the ones on
the counter. If one of them turns out to be reached for often enough, moving it
into the CLI is a small change; the other direction is not.

They are still held to the repository's standards — `ruff`, `mypy` and tests run
over this directory like any other.

## What is here

| Script | What it does |
| --- | --- |
| `prune_small_payloads.py` | Discards stored files below a size, the way the interface would. For the thumbnails and icons collected before `min_download_size` existed (ADR-042). |
| `survey_library.py` | Describes the whole shelf: how much, of what, how big, and how many pixels the images have. Reports only. |

`_shelf.py` is not a script — it holds the four lines each of them needs to find
a library and read all of it, and the underscore keeps it out of the pass that
tests the others.

## What the survey is for

Two settings were given starting values rather than measured ones, and the
survey is how they stop being guesses.

`preview_inline_bytes` decides whether a tile shows an image or a symbol in its
place. Whether one megabyte is right depends on what the library actually holds,
and "shown as themselves / shown as a symbol" says so directly.

Whether **thumbnails** would be worth generating turns on pixels rather than
bytes: a 300 KB photograph at 6000x4000 becomes a 96 MB bitmap in a browser, and
sixty of those is not a page. So the survey reads image headers — PNG, GIF and
JPEG, a few hundred bytes each, no decoding and no dependency — and prints the
distribution. A library that is mostly web JPEGs under a megapixel does not need
thumbnails; one full of camera originals cannot do without them.
