-- Create "rotation_data" table
CREATE TABLE `rotation_data` (
  `name` varchar(32) NOT NULL,
  `data` json NOT NULL,
  `updated_at` datetime NULL,
  PRIMARY KEY (`name`)
);
