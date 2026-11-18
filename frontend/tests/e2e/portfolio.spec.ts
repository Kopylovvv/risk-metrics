import { test, expect } from '@playwright/test';

test.describe('Основной пользовательский сценарий (E2E)', () => {
  test('Полный цикл: добавление, правка, удаление, расчёт, partial без итога, устаревание и перезагрузка', async ({ page }) => {
    // 1. Открытие страницы: чистая сессия и пустой портфель
    await page.goto('/');
    await expect(page.locator('#trade-form')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('#empty-trades')).toHaveText('Нет сделок в портфеле');
    await expect(page.locator('#calculate-btn')).toBeDisabled();

    // 2. Добавление первой сделки: ОФЗ 26238 (+100 шт, лонг)
    await page.locator('#isin-input').fill('RU000A1038V6');
    await page.locator('#isin-input').blur();
    await page.locator('#quantity-input').fill('100');
    await page.locator('#add-trade-btn').click();

    // Проверяем появление сделки в списке
    await expect(page.locator('#trade-list')).toContainText('RU000A1038V6');
    await expect(page.locator('#trade-list')).toContainText('100');
    await expect(page.locator('text=Сделок в портфеле: 1')).toBeVisible();

    // 3. Добавление второй сделки: ГазКЗ-34Д (-20 шт, шорт)
    await page.locator('#isin-input').fill('RU000A105A95');
    await page.locator('#isin-input').blur();
    await page.locator('#quantity-input').fill('-20');
    await page.locator('#add-trade-btn').click();

    await expect(page.locator('#trade-list')).toContainText('RU000A105A95');
    await expect(page.locator('text=Сделок в портфеле: 2')).toBeVisible();

    // 4. Редактирование первой сделки: 100 -> 150
    const editBtn = page.locator('#trade-list button[title="Редактировать"]').first();
    await editBtn.click();
    const editInput = page.locator('#trade-list input[type="number"]');
    await expect(editInput).toBeVisible();
    await editInput.fill('150');
    const saveBtn = page.locator('#trade-list button[title="Сохранить"]');
    await saveBtn.click();
    await expect(page.locator('#trade-list')).toContainText('150');

    // 5. Удаление второй сделки (ГазКЗ-34Д)
    const removeBtn = page.locator('#trade-list button[title="Удалить сделку"]').last();
    await removeBtn.click();
    await expect(page.locator('#trade-list')).not.toContainText('RU000A105A95');
    await expect(page.locator('text=Сделок в портфеле: 1')).toBeVisible();

    // 6. Запуск расчёта (полный расчёт success)
    await expect(page.locator('#calculate-btn')).toBeEnabled();
    await page.locator('#calculate-btn').click();

    // Проверяем успешный результат
    await expect(page.locator('text=Полный расчёт')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('#total-dv01')).toBeVisible();
    await expect(page.locator('text=Справедливая стоимость позиции (CurrentFairValue)')).toBeVisible();
    await expect(page.locator('table')).toContainText('RU000A1038V6');
    await expect(page.locator('table')).toContainText('150');
    await expect(page.locator('table')).toContainText('Ок');

    // 7. Проверка устаревания (isStale) при изменении состава портфеля
    await page.locator('#isin-input').fill('RU000A105A95');
    await page.locator('#quantity-input').fill('50');
    await page.locator('#add-trade-btn').click();
    await expect(page.locator('text=Сделок в портфеле: 2')).toBeVisible();

    // После добавления сделки результат должен мгновенно пометиться устаревшим
    await expect(page.locator('#stale-badge')).toHaveText('Результат устарел');
    await expect(page.locator('#portfolio-stale-warning')).toBeVisible();
    await expect(page.locator('#portfolio-stale-warning')).toContainText('Состав или параметры портфеля изменены');

    // 8. Проверка partial без общего итога (сделка без дюрации RU000A0JX0J2)
    await page.locator('#isin-input').fill('RU000A0JX0J2');
    await page.locator('#quantity-input').fill('10');
    await page.locator('#add-trade-btn').click();
    await expect(page.locator('text=Сделок в портфеле: 3')).toBeVisible();

    // Пересчитываем
    await page.locator('#calculate-btn').click();

    // Ожидаем статус partial
    await expect(page.locator('text=Частичный расчёт')).toBeVisible({ timeout: 10_000 });
    // Общий итог DV01 НЕ должен отображаться, вместо него сообщение
    await expect(page.locator('#total-dv01')).not.toBeVisible();
    await expect(page.locator('text=Итог не рассчитан: по части сделок нет результата')).toBeVisible();
    // Блок суммарного CFV должен быть скрыт при partial
    await expect(page.locator('text=Справедливая стоимость позиции (CurrentFairValue)')).not.toBeVisible();
    // В таблице должна быть строка с ошибкой "Нет данных"
    await expect(page.locator('table')).toContainText('Нет данных');

    // 9. Восстановление состояния после перезагрузки страницы (F5)
    await page.reload();
    await expect(page.locator('#trade-list')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('text=Сделок в портфеле: 3')).toBeVisible();
    await expect(page.locator('#trade-list')).toContainText('RU000A1038V6');
    await expect(page.locator('#trade-list')).toContainText('RU000A105A95');
    await expect(page.locator('#trade-list')).toContainText('RU000A0JX0J2');

    // Результат последнего расчёта восстановился (partial и сообщение об ошибке)
    await expect(page.locator('text=Частичный расчёт')).toBeVisible();
    await expect(page.locator('text=Итог не рассчитан: по части сделок нет результата')).toBeVisible();
  });
});
