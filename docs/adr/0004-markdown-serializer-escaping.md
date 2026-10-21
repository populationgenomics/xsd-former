# 4. Escaping in the markdown serializer

Date: 2026-10-21

## Status

Accepted

## Context

`serialize_content: markdown` renders an element's inline markup as markers
(`*`, `**`, `__`, `^(...)`, `~(...)`) via `_serialize_markdown`. Text was copied
through unchanged, so a literal marker character was indistinguishable from
markup. PubMed text is full of literal asterisks and underscores in HGVS
notation: PMID 34207366's title reads `*Cis*-Segregation ... (p.R391*) in
*SERPINC1* Gene`, and PMID 39705914 has `c.2568_2569del p.Gly857Alafs*27`. A
CommonMark renderer can pair two literal asterisks as emphasis (PMID 26586665's
`BRCA2*c.9976A>T; p.Lys3326*` renders `c.9976A>T; p.Lys3326` in italics), and
a consumer that strips markers to get plain text deletes every stop codon.

## Decision

Text is backslash-escaped wherever a literal character could be read as a
marker; markers never are.

- `\`, `*`, `_`, `^` and `~` are escaped everywhere in text. These are the
  escape character plus every character that starts or forms a marker.
- `)` is escaped only in text inside a `^(...)` or `~(...)`, where an unescaped
  `)` closes the script. Outside a script it ends nothing.
- `(` is never escaped. It opens a script only right after an unescaped `^` or
  `~`, and text never contains one. Escaping parentheses would also emit `\(`,
  the inline-math opener of MathJax/KaTeX-enabled renderers. A consumer that
  finds a script's end by balancing brackets misreads `Pax6^((+/-\))`; the
  documented scan does not balance brackets.
- The escapes are CommonMark backslash escapes, so a CommonMark renderer shows
  those characters literally.

Other CommonMark syntax in the text is not neutralised: a backtick pair forms a
code span (inside which the escapes show literally), `<...>` can form raw HTML
or an autolink, `&name;` is an entity, and a line-leading `#`, `>` or list
marker forms a block. Escaping `` ` ``, `<` and `&` too would close the inline
cases at the cost of `p\<0.05` and `R\&D` in ordinary abstracts; it is not
done.

The generated converter exports `markdown_to_plain_text`, the inverse to plain
text, so consumers do not reimplement it. It is one left-to-right scan: `\`
plus a character is that literal character; an unescaped `*` or `_` is dropped;
`^(` or `~(` opens a script and is dropped; an unescaped `)` closes the
innermost open script and is dropped, or is literal when none is open. The
markup tree is not recoverable (`<i>a</i><i>b</i>` gives `*a**b*`), only the
text content.

## Consequences

- Serialized values change wherever text contains an escaped character, so a
  generated package rebuilt on this version emits different strings for the
  same XML. Consumers that strip or parse the markers must unescape, or call
  `markdown_to_plain_text`.
- `test_markdown_to_plain_text_recovers_the_text_content` checks that
  `markdown_to_plain_text` applied to `_serialize_markdown`'s output returns the
  element's `itertext()`.
