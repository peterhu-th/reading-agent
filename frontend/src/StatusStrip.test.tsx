import { render, screen } from "@testing-library/react";
import { StatusStrip } from "./StatusStrip";

test("shows the current retrieval status", () => {
  render(<StatusStrip stage="checking" message="正在检查证据覆盖" />);
  expect(screen.getByText("检查证据")).toBeInTheDocument();
  expect(screen.getByText("正在检查证据覆盖")).toBeInTheDocument();
});
