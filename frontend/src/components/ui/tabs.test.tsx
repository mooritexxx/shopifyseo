import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "./tabs";

it("reveals the selected scrollable tab while preserving keyboard navigation", async () => {
  const reveal = vi.mocked(Element.prototype.scrollIntoView);
  reveal.mockClear();
  render(<Tabs defaultValue="seed">
    <TabsList scrollable aria-label="Research">
      <TabsTrigger value="seed">Seeds</TabsTrigger>
      <TabsTrigger value="target">Targets</TabsTrigger>
    </TabsList>
    <TabsContent value="seed">Seed controls</TabsContent>
    <TabsContent value="target">Target controls</TabsContent>
  </Tabs>);
  const user = userEvent.setup();
  await user.tab();
  await user.keyboard("{ArrowRight}");
  expect(screen.getByRole("tab", { name: "Targets" })).toHaveFocus();
  expect(screen.getByRole("tabpanel")).toHaveTextContent("Target controls");
  await waitFor(() => expect(reveal.mock.contexts).toContain(screen.getByRole("tab", { name: "Targets" })));
});
