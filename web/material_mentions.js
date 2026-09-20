const IMAGE_MENTION = /@图(\d+)/g;

export function mentionedImageNumbers(text) {
  const numbers = new Set();
  for (const match of String(text || "").matchAll(IMAGE_MENTION)) numbers.add(Number(match[1]));
  return numbers;
}

export function remapImageMentions(text, before, after) {
  return String(text || "").replace(IMAGE_MENTION, (token, rawNumber) => {
    const name = before[Number(rawNumber) - 1];
    if (!name) return token;
    const next = after.indexOf(name);
    return next < 0 ? token : `@图${next + 1}`;
  });
}

export function insertImageMention(text, start, end, number) {
  const value = String(text || "");
  const left = Math.max(0, Math.min(Number(start) || 0, value.length));
  const right = Math.max(left, Math.min(Number(end) || left, value.length));
  const token = `@图${number}`;
  return {value: value.slice(0, left) + token + value.slice(right), cursor: left + token.length};
}
