import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { ArticleIdeasPage } from "./article-ideas-page";

afterEach(() => vi.unstubAllGlobals());
it("supports keyboard queue switching, sorting and opening an idea without selecting it", async () => {
  const items = [
    {id:1,suggested_title:"Approved plan",brief:"",status:"approved",created_at:1},
    {id:2,suggested_title:"Alpha plan",brief:"",status:"idea",created_at:2},
    {id:3,suggested_title:"Zulu plan",brief:"",status:"idea",created_at:3},
  ];
  vi.stubGlobal("fetch", vi.fn(async () => ({ok:true,text:async()=>JSON.stringify({ok:true,data:{items,total:3}})})));
  render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}>
    <MemoryRouter initialEntries={["/article-ideas"]}><Routes>
      <Route path="/article-ideas" element={<ArticleIdeasPage />} />
      <Route path="/article-ideas/:id" element={<h1>Idea detail</h1>} />
    </Routes></MemoryRouter>
  </QueryClientProvider>);
  const user = userEvent.setup();
  await screen.findByRole("link",{name:"Approved plan"});
  screen.getByRole("tab",{name:"Approved"}).focus();
  await user.keyboard("{ArrowRight}");
  expect(await screen.findByRole("link",{name:"Alpha plan"})).toBeVisible();
  screen.getByRole("button",{name:"Title"}).focus();
  await user.keyboard("{Enter}");
  const rows = screen.getAllByRole("row");
  expect(within(rows[1]).getByRole("link",{name:"Zulu plan"})).toBeVisible();
  expect(screen.getByRole("checkbox",{name:"Select Zulu plan"})).not.toBeChecked();
  screen.getByRole("link",{name:"Zulu plan"}).focus();
  await user.keyboard("{Enter}");
  expect(screen.getByRole("heading",{name:"Idea detail"})).toBeVisible();
});
