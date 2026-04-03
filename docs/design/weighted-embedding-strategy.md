# Weighted Embedding Strategy

## Problem Statement

The semantic search indexer creates embeddings from entire markdown file content. This leads to suboptimal search results because:

1. **Signal dilution**: A 2000-word document might have only 10-20 words (filename, title, tags) that truly describe its topic. These meaningful words get drowned by body content that may mention many tangential topics.

2. **Relevant pages rank low**: Files with highly descriptive filenames and metadata often don't appear at the top of results because their semantic signal is diluted.

3. **Long documents dominate unpredictably**: More words create more "semantic surface area" but also more noise.

## Solution: Weighted Text Concatenation

Instead of embedding raw file content, we construct a weighted text representation by repeating important components before creating the embedding.

### How It Works

When SentenceTransformers encodes text, words appearing multiple times have proportionally more influence on the resulting vector direction. By repeating high-value components, we shift the embedding toward the true topic.

### Input Construction

```
{filename} {filename} {filename}
{metadata_title} {metadata_title} {metadata_title}
{metadata_tags} {metadata_tags}
{first_heading} {first_heading}
{body_content_truncated}
```

### Component Weights

| Component | Weight | Rationale |
|-----------|--------|-----------|
| Filename (without extension) | 3x | Often the most descriptive identifier; users name files meaningfully |
| Metadata title | 3x | Explicit topic declaration in frontmatter |
| Metadata tags/aliases | 2x | Explicit categorization by the author |
| First heading (# Title) | 2x | Usually the document title, reinforces filename |
| Body (first 500 words) | 1x | Supporting context, truncated to prevent dilution |

### Example

**File**: `API-Authentication-Guide.md`

**Frontmatter**:
```yaml
---
title: API Authentication Guide
tags: [api, authentication, security, oauth]
aliases: [Auth Guide]
---
```

**Content**:
```markdown
# API Authentication Guide

This guide covers how to authenticate with our API...
(2000 more words)
```

**Weighted Text for Embedding**:
```
API Authentication Guide API Authentication Guide API Authentication Guide
API Authentication Guide API Authentication Guide API Authentication Guide
api authentication security oauth api authentication security oauth
API Authentication Guide API Authentication Guide
This guide covers how to authenticate with our API...
(first 500 words of body)
```

## Benefits

1. **Single embedding per file**: No increase in vector storage or search complexity
2. **No change to search logic**: Same FAISS index, same search API
3. **Tunable**: Weights can be adjusted based on empirical results
4. **Short docs benefit naturally**: Higher signal-to-noise ratio
5. **Long docs don't dominate**: Body truncation prevents dilution

## Trade-offs

1. **Requires re-indexing**: Existing indexes must be rebuilt after implementation
2. **Metadata parsing overhead**: Small increase in indexing time for YAML parsing
3. **Weight tuning**: Optimal weights may vary by corpus; defaults are a starting point

## Implementation Notes

### Filename Processing

- Remove file extension (`.md`)
- Replace `-` and `_` with spaces
- Example: `API-Authentication-Guide.md` → `API Authentication Guide`

### Metadata Extraction

Parse YAML frontmatter (content between `---` markers) and extract:
- `title`: String value
- `tags`: List of strings
- `aliases`: List of strings

### Body Truncation

- Remove frontmatter before counting
- Take first 500 words (split on whitespace)
- This captures introduction/summary which typically contains key information

### Heading Extraction

- Find first line starting with `# ` (H1 heading)
- Strip the `# ` prefix
- If no H1 found, skip this component

## Future Enhancements

1. **Configurable weights**: Allow users to tune weights via configuration
2. **Section-aware indexing**: Create separate entries for major sections in long documents
3. **Keyword boosting**: Post-search score adjustment for exact matches in filename/title
4. **Length normalization**: Explicit scoring penalty for very long documents
