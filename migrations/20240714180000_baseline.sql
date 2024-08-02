CREATE TABLE `mirrored_channel`(
  src_id BIGINT NOT NULL,
  dest_id BIGINT NOT NULL,
  dest_server_id BIGINT,
  legacy BOOL,
  enabled BOOL,
  legacy_error_rate INTEGER,
  legacy_disable_for_failure_on_date DATETIME,
  PRIMARY KEY(src_id, dest_id),
  CONSTRAINT _mir_ids_uc UNIQUE(src_id, dest_id)
);

CREATE TABLE `mirrored_message`(
  dest_msg BIGINT NOT NULL auto_increment,
  dest_ch BIGINT,
  source_msg BIGINT,
  src_ch BIGINT,
  creation_datetime DATETIME,
  PRIMARY KEY(dest_msg)
);

CREATE TABLE `server_statistics`(
  id BIGINT NOT NULL auto_increment,
  population BIGINT,
  PRIMARY KEY(id)
);

CREATE TABLE `user_command`(
  id INTEGER NOT NULL auto_increment,
  l1_name VARCHAR(32),
  l2_name VARCHAR(32),
  l3_name VARCHAR(32),
  description VARCHAR(256),
  response_type INTEGER,
  response_data TEXT,
  PRIMARY KEY(id),
  CONSTRAINT _ln_name_uc UNIQUE(l1_name, l2_name, l3_name),
  CHECK(l3_name = '' OR response_type <> 0),
  CHECK((l2_name = '' AND l3_name = '') OR(l2_name <> ''))
);

CREATE TABLE `auto_post_settings`(
  name VARCHAR(32) NOT NULL,
  enabled BOOL,
  PRIMARY KEY(name)
);

CREATE TABLE `bungie_credentials`(
  id INTEGER NOT NULL auto_increment,
  refresh_token VARCHAR(1024),
  refresh_token_expires DATETIME,
  PRIMARY KEY(id)
); 