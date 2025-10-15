# XSDFormer

XSDFormer deforms [XML schema definitions (XSDs)](https://www.w3.org/XML/Schema) into [Protobuf definitions](https://protobuf.dev/). It also generates Python code to convert parsed XML into the corresponding Protobuf representation.

It supports enough of the XSD specification to convert the [ClinVar](https://www.ncbi.nlm.nih.gov/clinvar/) and [BioC](http://bioc.sourceforge.net/) schemas; full support for all XSD features is a non-goal.

## Why convert from XSD to Protobuf?

While XML and XSDs (or alternatively JSON and JSON schemas) are powerful for defining complex, human-readable data structures, they have some drawbacks, especially in high-performance applications or when dealing with large datasets.

### Performance and Size

*   **Parsing Speed:** XML is a text-based format and can be slow to parse. Protobuf is a binary format that is designed for speed and efficiency. Converting XML data to Protobuf can result in significantly faster parsing times.
*   **Storage Space:** XML is verbose, with opening and closing tags that add to the file size. Protobuf's binary format is much more compact, leading to smaller file sizes. This is a major advantage for storing large datasets or for transmitting data over a network. Effectively compressing XML requires a schema-specific dictionary, or compressing multiple records together so that the dictionary of tag/key names can be reused. However this limits the possibility for random access enabled by compressing records individually.

### Developer Experience

*   **Generated Code:** Protobuf compilers generate code in many languages, providing a simple and consistent way to work with the data.
*   **Type Safety:** The Protobuf schema provides strong typing, which can help to prevent bugs. Parsing the wire format requires the protobuf definition, meaning that the data is tied to its typed representation. Conversely XML representations are only optionally validated by a schema, and so by default type information is lost during parsing (everything is treated as text).

By converting XSDs to Protobuf definitions, `xsd-former` allows developers to take advantage of the benefits of Protobuf while still working with data that is originally defined in an XML schema.
