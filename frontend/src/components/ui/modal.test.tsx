import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { Modal } from "./modal";

function Gallery() {
  const [open, setOpen] = useState(false);
  return <>
    <button onClick={() => setOpen(true)}>Preview image</button>
    <Modal open={open} onOpenChange={setOpen} title="Image preview" description="Product gallery">
      <button>Next image</button>
    </Modal>
  </>;
}

it("returns keyboard focus to the opener after Escape closes a controlled modal", async () => {
  const user = userEvent.setup();
  render(<Gallery />);
  const opener = screen.getByRole("button", { name: "Preview image" });
  await user.click(opener);
  expect(screen.getByRole("dialog", { name: "Image preview" })).toBeVisible();
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  await waitFor(() => expect(opener).toHaveFocus());
});
