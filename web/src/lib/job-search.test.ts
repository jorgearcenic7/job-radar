import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DEFAULT_COUNTRY,
  hasActiveFilters,
  jobSearchHref,
  parseFilters,
  parsePage,
  type JobFilters,
} from "./job-search.ts";

const DEFAULT_FILTERS: JobFilters = {
  q: "",
  company: "",
  country: DEFAULT_COUNTRY,
  selected: false,
};

test("parseFilters aplica los valores por defecto", () => {
  assert.deepEqual(parseFilters({}), DEFAULT_FILTERS);
});

test("parseFilters recorta espacios y limita a 120 caracteres", () => {
  const filters = parseFilters({
    q: "  Data Engineer  ",
    company: ` ${"x".repeat(200)} `,
    country: "  Portugal ",
  });

  assert.equal(filters.q, "Data Engineer");
  assert.equal(filters.company.length, 120);
  assert.equal(filters.country, "Portugal");
});

test("parseFilters conserva el país vacío de 'Todos los países'", () => {
  assert.equal(parseFilters({ country: "" }).country, "");
});

test("parseFilters ignora parámetros repetidos", () => {
  // ?q=a&q=b llega como array: se trata como ausente.
  const filters = parseFilters({
    q: ["a", "b"],
    company: ["Stripe"],
    country: ["Spain", "Portugal"],
    selected: ["1", "1"],
  });

  assert.deepEqual(filters, DEFAULT_FILTERS);
});

test("parseFilters solo activa coincidencias con selected=1", () => {
  assert.equal(parseFilters({ selected: "1" }).selected, true);

  for (const value of ["0", "true", "yes", "", " 1"]) {
    assert.equal(parseFilters({ selected: value }).selected, false, value);
  }
});

test("parsePage acepta páginas válidas y acota a 500", () => {
  assert.equal(parsePage(undefined), 1);
  assert.equal(parsePage("1"), 1);
  assert.equal(parsePage("7"), 7);
  assert.equal(parsePage("500"), 500);
  assert.equal(parsePage("501"), 500);
  assert.equal(parsePage("999999999999"), 500);
});

test("parsePage vuelve a la primera página ante valores inválidos", () => {
  for (const value of ["0", "-2", "abc", "", "NaN", "Infinity", ["2"]]) {
    assert.equal(parsePage(value), 1, JSON.stringify(value));
  }
});

test("parsePage conserva el prefijo numérico como parseInt", () => {
  assert.equal(parsePage("12abc"), 12);
  assert.equal(parsePage("3.9"), 3);
});

test("hasActiveFilters detecta cualquier filtro distinto del inicial", () => {
  assert.equal(hasActiveFilters(DEFAULT_FILTERS), false);
  assert.equal(hasActiveFilters({ ...DEFAULT_FILTERS, q: "data" }), true);
  assert.equal(
    hasActiveFilters({ ...DEFAULT_FILTERS, company: "Stripe" }),
    true,
  );
  assert.equal(hasActiveFilters({ ...DEFAULT_FILTERS, country: "" }), true);
  assert.equal(
    hasActiveFilters({ ...DEFAULT_FILTERS, selected: true }),
    true,
  );
});

test("jobSearchHref envía siempre el país, incluso vacío", () => {
  assert.equal(jobSearchHref(DEFAULT_FILTERS, 1), "/?country=Spain");
  assert.equal(
    jobSearchHref({ ...DEFAULT_FILTERS, country: "" }, 1),
    "/?country=",
  );
});

test("jobSearchHref omite la página 1 y conserva el orden de parámetros", () => {
  const filters: JobFilters = {
    q: "data engineer",
    company: "Perk (TravelPerk)",
    country: "Spain",
    selected: true,
  };

  assert.equal(
    jobSearchHref(filters, 2),
    "/?q=data+engineer&company=Perk+%28TravelPerk%29&country=Spain" +
      "&selected=1&page=2",
  );
  assert.ok(!jobSearchHref(filters, 1).includes("page="));
});

test("jobSearchHref codifica caracteres especiales", () => {
  const href = jobSearchHref(
    { ...DEFAULT_FILTERS, q: "a&b=c?#/ñ" },
    1,
  );
  const params = new URLSearchParams(href.slice(2));

  assert.equal(params.get("q"), "a&b=c?#/ñ");
  assert.equal(params.get("country"), "Spain");
});

test("parseFilters y jobSearchHref son simétricos", () => {
  const filters: JobFilters = {
    q: "analytics",
    company: "Ebury",
    country: "",
    selected: true,
  };
  const params = Object.fromEntries(
    new URLSearchParams(jobSearchHref(filters, 3).slice(2)),
  );

  assert.deepEqual(parseFilters(params), filters);
  assert.equal(parsePage(params.page), 3);
});
