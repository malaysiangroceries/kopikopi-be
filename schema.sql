-- Kopi Kopi schema (MySQL 8+)

CREATE TABLE IF NOT EXISTS customer (
  id            INT NOT NULL AUTO_INCREMENT,
  business_name VARCHAR(150) NOT NULL,
  dealer_name   VARCHAR(100) NOT NULL,
  email         VARCHAR(120) NULL DEFAULT NULL,
  phone_number  VARCHAR(25) NOT NULL,
  address       VARCHAR(255) NULL DEFAULT NULL,
  complete_deal JSON NULL DEFAULT NULL,
  pending_deal  JSON NULL DEFAULT NULL,
  created_at    DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS orders (
  id            INT NOT NULL AUTO_INCREMENT,
  ref_num       VARCHAR(20) NOT NULL,
  date_created  DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
  customer_name VARCHAR(100) NOT NULL,
  customer_id   INT NULL DEFAULT NULL,
  amount        DECIMAL(10,2) NULL DEFAULT 0.00,
  items         JSON NOT NULL,
  status        ENUM('Delivery','Pending','Invoice') NOT NULL,
  support_docs  VARCHAR(255) NULL DEFAULT NULL,
  invoice_sent  ENUM('True','False') NULL DEFAULT 'False',
  paid          ENUM('True','False') NULL DEFAULT 'False',
  PRIMARY KEY (id),
  UNIQUE KEY uk_orders_ref_num (ref_num),
  KEY idx_orders_customer_id (customer_id),
  CONSTRAINT fk_orders_customer
    FOREIGN KEY (customer_id) REFERENCES customer(id)
    ON UPDATE CASCADE
    ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS code_verify (
  id         INT NOT NULL AUTO_INCREMENT,
  identifier VARCHAR(100) NOT NULL,
  code       VARCHAR(20)  NOT NULL,
  status     TINYINT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at DATETIME NOT NULL,
  used_at    DATETIME NULL DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_code_verify_identifier_code (identifier, code),
  KEY idx_code_verify_identifier (identifier),
  KEY idx_code_verify_status_expires (status, expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DELIMITER $$
CREATE TRIGGER trg_code_verify_before_insert
BEFORE INSERT ON code_verify
FOR EACH ROW
BEGIN
  IF NEW.expires_at IS NULL THEN
    SET NEW.expires_at = DATE_ADD(NOW(), INTERVAL 5 MINUTE);
  END IF;
END$$
DELIMITER ;

DELIMITER $$
CREATE EVENT IF NOT EXISTS ev_code_verify_expire
ON SCHEDULE EVERY 1 MINUTE
DO
BEGIN
  UPDATE code_verify
  SET status = 2
  WHERE status = 0
    AND expires_at <= NOW();
END$$
DELIMITER ;

CREATE TABLE IF NOT EXISTS menu (
  id           INT NOT NULL AUTO_INCREMENT,
  name         VARCHAR(150) NOT NULL,
  category     VARCHAR(100) NULL DEFAULT NULL,
  price        DECIMAL(10,2) NOT NULL,
  ingredients  JSON NULL DEFAULT NULL,
  description  TEXT NULL,
  image_url    VARCHAR(255) NULL DEFAULT NULL,
  is_available TINYINT NOT NULL DEFAULT 1,
  sort_order   INT NULL DEFAULT 0,
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_menu_category (category),
  KEY idx_menu_available_sort (is_available, sort_order)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
