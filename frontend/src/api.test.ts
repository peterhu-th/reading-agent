import { parseEventBlock } from "./api";

test("parses a server-sent event", () => {
  const received: Array<[string, Record<string, unknown>]> = [];
  parseEventBlock('event: answer_delta\ndata: {"text":"你好"}', (event, data) => received.push([event, data]));
  expect(received).toEqual([["answer_delta", { text: "你好" }]]);
});
