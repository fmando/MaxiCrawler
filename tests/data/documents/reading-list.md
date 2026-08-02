# Reading list

A few links worth keeping while working on the discovery engine.

## Specifications

- [URL Living Standard](https://spec.example.test/url) — the normalization rules
  we follow.
- [Robots exclusion](https://spec.example.test/robots) — required reading before
  the HTTP engine lands.

## Autolinks and bare URLs

Autolink syntax works too: <https://blog.example.test/parsing-urls>

A bare URL in a sentence, followed by a full stop: https://blog.example.test/normalization.

## Reference-style links

See the [archive][1] and the [mirror][2].

[1]: https://archive.example.test/papers "Paper archive"
[2]: http://mirror.example.test/papers

## Repetition

The URL standard is linked twice: [again](https://spec.example.test/url)

## Things that are not links

Relative paths such as [the roadmap](../ROADMAP.md) cannot be resolved without
a base URL, and `https://example.test/in-code-span` sits inside a code span.
