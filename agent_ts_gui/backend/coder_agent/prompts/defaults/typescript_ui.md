---
name: typescript_ui
description: TypeScript / frontend UI work
keywords: typescript, react, vue, css, html, frontend, 界面, 前端, 组件, component, tsx
globs: *.ts,*.tsx,*.js,*.jsx,*.vue,*.css,*.html
effort: medium
---
Task type: TypeScript / UI.
- Check package.json and tsconfig.json first: framework, scripts, strictness. Use the existing package manager.
- Use strict types; avoid `any`. Keep components small and typed; follow the project's existing state-management and styling approach.
- Verify with the project's scripts (npm run build / npm test / npx tsc --noEmit).
- Do not add dependencies without telling the user.
