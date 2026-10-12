import { render } from "@testing-library/react";

import { HighlightedText } from "./HighlightedText";

test("search highlights a literal keyword without interpreting markup or regex characters", () => {
  const view = render(<HighlightedText text={'<img src=x onerror="alert(1)"> a+b A+B'} query="a+b" />);
  expect(view.container.querySelector("img")).toBeNull();
  expect(view.container.textContent).toBe('<img src=x onerror="alert(1)"> a+b A+B');
  expect(Array.from(view.container.querySelectorAll("mark"), (node) => node.textContent)).toEqual(["a+b", "A+B"]);
});
