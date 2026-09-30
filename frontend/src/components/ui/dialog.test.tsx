import { useRef, useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./dialog";

function ControlledDialog({ customFocus = false }: { customFocus?: boolean }) {
  const [open, setOpen] = useState(false);
  const next = useRef<HTMLButtonElement>(null);
  return <>
    <button onClick={() => setOpen(true)}>Open review</button>
    <button ref={next}>Next step</button>
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent onCloseAutoFocus={customFocus ? event => {event.preventDefault(); next.current?.focus();} : undefined}>
        <DialogTitle>Review</DialogTitle><DialogDescription>Review details</DialogDescription>
        <button onClick={() => setOpen(false)}>Done</button>
      </DialogContent>
    </Dialog>
  </>;
}
it("restores focus after Escape for a dialog opened without DialogTrigger", async () => {
  const user = userEvent.setup(); render(<ControlledDialog />);
  const opener = screen.getByRole("button",{name:"Open review"});
  await user.click(opener);
  expect(screen.getByRole("dialog")).toBeVisible();
  await user.keyboard("{Escape}");
  await waitFor(()=>expect(opener).toHaveFocus());
});
it("preserves an explicit focus destination supplied by the caller", async () => {
  const user = userEvent.setup(); render(<ControlledDialog customFocus />);
  await user.click(screen.getByRole("button",{name:"Open review"}));
  await user.click(screen.getByRole("button",{name:"Done"}));
  await waitFor(()=>expect(screen.getByRole("button",{name:"Next step"})).toHaveFocus());
});
