async function j(url, opts) {
  const r = await fetch(url, opts);
  const body = await r.json();
  if (!r.ok) { const e = new Error(body.error || r.status); e.cls = body.error_class; throw e; }
  return body;
}
function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "disabled") el.disabled = !!v;
    else if (k === "checked") el.checked = !!v;
    else el.setAttribute(k, v);
  }
  for (const kid of kids) if (kid !== null && kid !== undefined) el.append(kid);
  return el;
}