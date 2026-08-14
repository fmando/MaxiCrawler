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
| `check_library.py` | Compares every record against the disk and reports where they have come apart. Repairs the two faults that are already decided. |
| `start_over.py` | Moves the library and its database aside under a timestamp and leaves an empty one in their place. Renames; never deletes. |

`_shelf.py` is not a script — it holds the four lines each of them needs to find
a library and read all of it, and the underscore keeps it out of the pass that
tests the others.

## Starting over

Emptying a library is two lines at a shell. What `start_over.py` adds is not the
deleting — it is the **not** deleting. The library and its database are renamed
under a timestamp, an empty library takes their place, and the commands to
change your mind are printed. The disk is exactly as full afterwards; freeing it
is a second, deliberate act by somebody who has already seen the new state work.

It refuses three ways before it touches anything: if the directory carries no
`library.json` descriptor, so a mistyped path moves nothing; if something is
already sitting at the names it would move to; and if the database will not open
for writing, which usually means the server is still running. That last check is
a hint and not a guarantee — on Linux a file can be renamed while it is open —
so stop the server yourself. A server writing into a database that has been
moved out from under it helps nobody.

## What the doctor will and will not do

The filesystem is the authority (ADR-010) and every record is a claim about it.
`check_library.py` walks the directories — not the index, which would only
repeat what it was told — and reports every place the two have come apart: a
record pointing at a file that is gone, a file no record mentions, a size or a
checksum that no longer matches, an interrupted download nobody swept up, a
document that cannot be read at all.

**It repairs only where the intention is already on record.** With `--apply` it
clears staging leftovers, which ADR-012 says are worthless the moment a transfer
stops, and it finishes discards whose file is somehow still there. That is the
whole list, and the boundary is deliberate: removing a file no record mentions,
or rewriting a record whose payload has gone, would be a *new* decision about
what should exist in your library. A maintenance script does not get to make
those. It reports them, and `--urls` prints what to queue again.

`--checksums` reads every byte of every file, which is worth doing after a disk
scare and not worth doing on a Tuesday. It is off by default for that reason.

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
