// Preserve legacy instructions visibly until the user edits the unified field.
export function shotRequestText(request, note, intent) {
  if (request !== undefined) return request;
  const identity = String(note || '').trim(), action = String(intent || '').trim();
  if (!identity || !action || identity === action) return action || identity;
  return `${identity}\n\n动作与运镜（优先）：${action}`;
}
