-- Review and run as the schema owner AFTER migrations, never at API startup.
-- Create LOGIN roles p5_api and p5_loader with distinct passwords first.
-- This file intentionally grants no CREATE, TRUNCATE, DELETE or role membership.
GRANT USAGE ON SCHEMA public TO p5_api, p5_loader;

REVOKE ALL ON TABLE mapping_state, mapping_current, run_records, source_snapshots,
  component_events, component_labels, approval_decisions,
  external_order_batches, external_order_releases,
  published_order_batches, published_orders,
  checkpoints, checkpoint_blobs, checkpoint_writes, checkpoint_migrations
  FROM p5_api, p5_loader;

GRANT SELECT, INSERT, UPDATE ON TABLE mapping_state, mapping_current, run_records,
  source_snapshots, component_events, component_labels, approval_decisions,
  checkpoints, checkpoint_blobs, checkpoint_writes TO p5_api;
GRANT SELECT ON TABLE checkpoint_migrations TO p5_api;
GRANT SELECT ON TABLE external_order_batches TO p5_api;
GRANT UPDATE (agent_run_id, dispatch_lease_until) ON TABLE external_order_batches TO p5_api;
GRANT SELECT, INSERT ON TABLE external_order_releases TO p5_api;
-- API cannot publish destination rows.

GRANT SELECT, INSERT ON TABLE external_order_batches TO p5_loader;
GRANT SELECT ON TABLE external_order_releases, mapping_state, run_records TO p5_loader;
GRANT SELECT, INSERT ON TABLE published_order_batches, published_orders TO p5_loader;
-- Loader cannot write mappings, releases, decisions or graph checkpoints.

GRANT USAGE ON SEQUENCE mapping_state_id_seq TO p5_api;
GRANT USAGE ON SEQUENCE component_events_id_seq TO p5_api;
GRANT USAGE ON SEQUENCE component_labels_id_seq TO p5_api;
