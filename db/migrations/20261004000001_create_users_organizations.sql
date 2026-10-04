-- migrate:up
CREATE TYPE organization_role AS ENUM ('ADMIN', 'DEVELOPER', 'QA');

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuidv7() CHECK (uuid_extract_version(id) = 7),
    email VARCHAR(254) NOT NULL UNIQUE CHECK (email = lower(email)),
    first_name VARCHAR(100) NOT NULL CHECK (length(trim(first_name)) BETWEEN 1 AND 100),
    last_name VARCHAR(100) NOT NULL CHECK (length(trim(last_name)) BETWEEN 1 AND 100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE organizations (
    id UUID PRIMARY KEY DEFAULT uuidv7() CHECK (uuid_extract_version(id) = 7),
    name VARCHAR(150) NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 150),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE user_organization_relation (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    role organization_role NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, organization_id)
);

CREATE INDEX user_organization_relation_organization_id_idx
    ON user_organization_relation (organization_id);

-- migrate:down
DROP TABLE user_organization_relation;
DROP TABLE organizations;
DROP TABLE users;
DROP TYPE organization_role;
