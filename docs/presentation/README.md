# Reveal.js presentation

Static presentation for GitHub Pages.

## Local preview

From the repository root:

```sh
python3 -m http.server 8000
```

Open:

```text
http://localhost:8000/docs/presentation/
```

## GitHub Pages

Recommended setup:

1. Push `docs/` to GitHub.
2. In repository settings, open **Pages**.
3. Set source to **Deploy from a branch**.
4. Select the branch with this repository and folder `/docs`.
5. The presentation will be available at:

```text
https://theramsay.github.io/ppp-project/presentation/
```

## Assets

The current SVGs in `assets/` are simple draft diagrams. Replace them with final
Excalidraw exports when ready, keeping the same filenames:

- `stencil.svg`
- `decomposition.svg`
- `iteration.svg`

For the actual defense, export a PDF backup from the browser print dialog or
Reveal.js PDF mode:

```text
http://localhost:8000/docs/presentation/?print-pdf
```
