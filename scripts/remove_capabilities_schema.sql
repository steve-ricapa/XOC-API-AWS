ALTER TABLE IF EXISTS integrations DROP COLUMN IF EXISTS capabilities;
ALTER TABLE IF EXISTS tickets DROP COLUMN IF EXISTS capability_level;
ALTER TABLE IF EXISTS tickets DROP COLUMN IF EXISTS capability_policy_snapshot;

DROP TABLE IF EXISTS integration_capability_template_assignments;
DROP TABLE IF EXISTS integration_capability_templates;
