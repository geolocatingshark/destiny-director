-- Create "command_usage" table
CREATE TABLE `command_usage` (
  `command_name` varchar(128) NOT NULL,
  `date` date NOT NULL,
  `count` bigint NOT NULL,
  PRIMARY KEY (`command_name`, `date`)
);
