/**
 * Pantalla /knowledge: asistente de la base de conocimiento RAG (Hito 7).
 *
 * Mismo enfoque que reportingDashboard.test.tsx: sin @testing-library,
 * montando con `act` + `createRoot` en jsdom. Solo `askKnowledgeBase` se
 * sustituye; `describeKnowledgeError` es el real.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import KnowledgeAssistantPage from "../app/knowledge/page";
import { ApiFieldError, ApiRequestError } from "../services/http";
import { askKnowledgeBase, describeKnowledgeError } from "../services/knowledgeApi";
import { EXAMPLE_QUESTIONS, MAX_QUESTION_LENGTH, toPlainText, validateQuestion } from "../types/knowledge";

jest.mock("../services/knowledgeApi", () => ({
  ...jest.requireActual("../services/knowledgeApi"),
  askKnowledgeBase: jest.fn(),
}));

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const mockedAsk = askKnowledgeBase as jest.MockedFunction<typeof askKnowledgeBase>;

let container: HTMLDivElement;
let root: Root;

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

async function renderPage() {
  await act(async () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    root.render(<KnowledgeAssistantPage />);
  });
}

async function typeQuestion(value: string) {
  const textarea = container.querySelector("textarea") as HTMLTextAreaElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, value);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function buttonByText(text: string): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll("button")).find((b) => b.textContent === text);
  if (!button) throw new Error(`No hay botón "${text}"`);
  return button;
}

async function click(button: HTMLButtonElement) {
  await act(async () => {
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  mockedAsk.mockReset();
});

describe("KnowledgeAssistantPage", () => {
  it("envía la pregunta sin espacios sobrantes y muestra la respuesta generada", async () => {
    let resolve: (answer: string) => void = () => undefined;
    mockedAsk.mockReturnValue(new Promise((r) => (resolve = r)));
    await renderPage();

    await typeQuestion("  ¿Qué debo traer a mi primera cita?  ");
    await click(buttonByText("Preguntar"));

    expect(mockedAsk).toHaveBeenCalledWith("¿Qué debo traer a mi primera cita?");
    expect(container.textContent).toContain("Consultando la base de conocimiento…");
    expect(buttonByText("Consultando…").disabled).toBe(true);

    await act(async () => resolve("Trae tu documento de identidad y la tarjeta del seguro."));
    await flush();

    expect(container.querySelector('[data-testid="knowledge-answer"]')?.textContent).toBe(
      "Trae tu documento de identidad y la tarjeta del seguro.",
    );
    expect(container.textContent).toContain("Pregunta: ¿Qué debo traer a mi primera cita?");
    expect(container.textContent).not.toContain("Consultando la base de conocimiento…");
  });

  it("no llama a la API con una pregunta vacía y explica por qué", async () => {
    await renderPage();
    await typeQuestion("    ");
    await click(buttonByText("Preguntar"));

    expect(mockedAsk).not.toHaveBeenCalled();
    expect(container.textContent).toContain("Escribe una pregunta antes de consultar.");
  });

  it("muestra un fallo como error con Reintentar, nunca como una respuesta vacía", async () => {
    mockedAsk.mockRejectedValueOnce(new ApiRequestError("El servidor tuvo un problema inesperado.", 503));
    mockedAsk.mockResolvedValueOnce("Con 12 horas de antelación se cobra el cargo de cancelación tardía.");
    await renderPage();

    await typeQuestion("¿Cobran por cancelar con 12 horas?");
    await click(buttonByText("Preguntar"));
    await flush();

    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain("El asistente no está disponible en este momento");
    expect(container.querySelector('[data-testid="knowledge-answer"]')).toBeNull();

    // Reintentar repite la última pregunta enviada aunque el campo cambie.
    await typeQuestion("otra cosa");
    await click(buttonByText("Reintentar"));
    await flush();

    expect(mockedAsk).toHaveBeenLastCalledWith("¿Cobran por cancelar con 12 horas?");
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(container.querySelector('[data-testid="knowledge-answer"]')?.textContent).toContain("cancelación tardía");
  });

  it("rellena el campo con una pregunta de ejemplo sin enviarla", async () => {
    await renderPage();
    await click(buttonByText(EXAMPLE_QUESTIONS[0]));

    expect((container.querySelector("textarea") as HTMLTextAreaElement).value).toBe(EXAMPLE_QUESTIONS[0]);
    expect(mockedAsk).not.toHaveBeenCalled();
  });

  it("recuerda no escribir datos de pacientes", async () => {
    await renderPage();
    expect(container.querySelector('[role="note"]')?.textContent).toContain("No escribas datos que identifiquen a un paciente");
  });
});

describe("validateQuestion", () => {
  it("acepta una pregunta normal y rechaza vacías o demasiado largas", () => {
    expect(validateQuestion("¿Aceptan Aetna?")).toBeNull();
    expect(validateQuestion("   ")).toBe("Escribe una pregunta antes de consultar.");
    expect(validateQuestion("a".repeat(MAX_QUESTION_LENGTH))).toBeNull();
    expect(validateQuestion("a".repeat(MAX_QUESTION_LENGTH + 1))).toContain(`${MAX_QUESTION_LENGTH} caracteres o menos`);
  });
});

describe("describeKnowledgeError", () => {
  it("distingue asistente no disponible, pregunta inválida y otros errores", () => {
    expect(describeKnowledgeError(new ApiRequestError("x", 503))).toContain("no está disponible");
    expect(describeKnowledgeError(new ApiFieldError([{ field: "question", message: "blank" }]))).toContain(
      "La pregunta no es válida",
    );
    expect(describeKnowledgeError(new Error("No se pudo conectar con el servidor."))).toBe("No se pudo conectar con el servidor.");
    expect(describeKnowledgeError("raro")).toBe("No se pudo obtener la respuesta. Inténtalo de nuevo.");
  });
});

describe("toPlainText", () => {
  it("quita negritas y títulos Markdown y conserva las viñetas", () => {
    expect(toPlainText("## Cargos\n- **Estados Unidos:** cargo de **50 USD**.\n- __Reino Unido__: 40 GBP.")).toBe(
      "Cargos\n- Estados Unidos: cargo de 50 USD.\n- Reino Unido: 40 GBP.",
    );
  });

  it("no toca un texto ya plano", () => {
    expect(toPlainText("No. Medicaid no está aceptado en Georgia.")).toBe("No. Medicaid no está aceptado en Georgia.");
  });
});
