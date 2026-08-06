# Private profile setup

Copy the three example files into the ignored `private/profile/` directory:

```text
private/profile/profile.md
private/profile/cv-layout.yaml
private/profile/cv-template.docx
```

Replace the synthetic profile text and template content with your own details.
Keep the file names unchanged. The application validates that these inputs are
regular files beneath `private/`, rejects symlinks, and never stores a
normalized copy of the profile.

The tracked AI configuration is in `config/ai.yaml`. API credentials do not
belong in either YAML file; provide `OPENAI_API_KEY` in the process environment.
