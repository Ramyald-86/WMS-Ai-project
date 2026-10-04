ОБНОВЛЕНИЕ PRINT AGENT НА САЙТЕ FULFILLCORE

Пакет меняет только Print Agent download/help:
- app/main.py: только имена текущего архива и инструкции;
- app/static/app.js: только надпись версии Print Agent;
- app/static/print-agent.html: подробная актуальная инструкция;
- app/downloads/: добавляет ZIP 1.2.5 и инструкцию.

Остальные модули FulfillCore не переписываются.
Перед изменением автоматически создаётся backup_print_agent_site_1_2_5_<дата>.

Установка на сервере после загрузки этого архива:
1. Распаковать архив в /opt/fulfillcore/print_agent_1_2_5_site
2. Выполнить:
   cd /opt/fulfillcore
   sh print_agent_1_2_5_site/deploy_print_agent_1_2_5_site.sh
3. Дождаться:
   OK: Print Agent 1.2.5 is published on FulfillCore site.
4. Открыть:
   https://201-24-52-218.nip.io/help/print-agent
5. Скачать архив уже через сайт и проверить установку на одном ПК.
