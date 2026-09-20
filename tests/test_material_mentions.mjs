import test from "node:test";
import assert from "node:assert/strict";
import {insertImageMention, mentionedImageNumbers, remapImageMentions} from "../web/material_mentions.js";

test("image mentions are detected without treating ordinary 图号 as mentions", () => {
  assert.deepEqual([...mentionedImageNumbers("@图2 是人物，图1是普通文字，@图2 再次出现")], [2]);
});

test("image mentions follow image identity when references are reordered", () => {
  assert.equal(
    remapImageMentions("@图1 是人物，@图3 是环境", ["person.png", "prop.png", "room.png"], ["room.png", "person.png", "prop.png"]),
    "@图2 是人物，@图1 是环境",
  );
});

test("picker insertion can replace a typed at sign", () => {
  assert.deepEqual(insertImageMention("人物来自@，环境稳定", 4, 5, 2), {
    value: "人物来自@图2，环境稳定",
    cursor: 7,
  });
});
