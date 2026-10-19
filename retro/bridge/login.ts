// Замена login.ts из netschool-retro: вход в «Сетевой город» у нас — в самом
// приложении (PIN, Госуслуги, выбор ученика), а не в окне этой страницы.
// Окно входа их сайт открывает, когда сервер ответил 401 или после «Выйти»,
// — отсюда возвращаемся в приложение (bridge.js).
export async function initLogin(): Promise<void> {
  (globalThis as unknown as { retroBridge: { toApp(): void } }).retroBridge.toApp();
}
