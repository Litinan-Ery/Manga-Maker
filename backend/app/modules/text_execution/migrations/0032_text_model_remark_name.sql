ALTER TABLE text_model_configs
ADD COLUMN remark_name TEXT
    CHECK (
        remark_name IS NULL
        OR (length(trim(remark_name)) BETWEEN 1 AND 200)
    );
