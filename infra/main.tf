# 1. Clé SSH
resource "aws_key_pair" "terraform_key" {
  key_name   = var.key_name
  public_key = file(var.public_key_path)
}

# 2. Groupe de sécurité
resource "aws_security_group" "ec2_sg" {
  name        = "ec2_sg"
  description = "Allow SSH and MongoDB"

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 27017
    to_port     = 27017
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# 3. IAM role pour autoriser EC2 à pull depuis ECR
resource "aws_iam_role" "ec2_role" {
  name = "ec2-ecr-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect = "Allow",
      Principal = {
        Service = "ec2.amazonaws.com"
      },
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecr_access" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_instance_profile" "ec2_profile" {
  name = "ec2-instance-profile"
  role = aws_iam_role.ec2_role.name
}

# 4. ECR pour ETL
resource "aws_ecr_repository" "etl_repo" {
  name                 = "greenandcoop-etl"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name = "greenandcoop-etl-repo"
  }
}

# 4bis. Build + push automatique de l'image ETL après création du repo
resource "null_resource" "push_etl_image" {
  depends_on = [aws_ecr_repository.etl_repo]

  provisioner "local-exec" {
    working_dir = "../"
    command = <<EOT
      ACCOUNT_ID=${var.account_id}
      REGION=${var.aws_region}
      REPO=${aws_ecr_repository.etl_repo.name}

      echo "==> Build de l'image ETL"
      docker compose build etl

      echo "==> Tag de l'image ETL"
      docker tag etl:latest $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$REPO:latest

      echo "==> Login ECR"
      aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com

      echo "==> Push de l'image ETL vers ECR"
      docker push $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$REPO:latest
    EOT
  }
}

# 5. EC2 qui déploie Mongo + ETL au boot
resource "aws_instance" "app_ec2" {
  ami                    = var.ami_id
  instance_type          = var.instance_type
  key_name               = aws_key_pair.terraform_key.key_name
  security_groups        = [aws_security_group.ec2_sg.name]
  iam_instance_profile   = aws_iam_instance_profile.ec2_profile.name
  depends_on             = [null_resource.push_etl_image]

  user_data = <<-EOF
    #!/bin/bash
    set -e

    apt-get update -y
    apt-get install -y docker.io awscli
    systemctl start docker
    systemctl enable docker

    # Créer un réseau Docker
    docker network create app-net

    # Démarrer Mongo depuis Docker Hub
    docker run -d \
      --name mongo \
      --network app-net \
      -p 27017:27017 \
      -e MONGO_INITDB_ROOT_USERNAME=${var.mongo_root_username} \
      -e MONGO_INITDB_ROOT_PASSWORD=${var.mongo_root_password} \
      -v /data/mongo:/data/db \
      mongo:8.0

    # Login à ECR et démarrer ETL
    aws ecr get-login-password --region ${var.aws_region} | docker login --username AWS --password-stdin ${var.account_id}.dkr.ecr.${var.aws_region}.amazonaws.com

    docker run -d \
      --name etl \
      --network app-net \
      -e MONGO_URI="mongodb://${var.mongo_root_username}:${var.mongo_root_password}@mongo:27017/greenandcoop?authSource=admin" \
      ${var.account_id}.dkr.ecr.${var.aws_region}.amazonaws.com/greenandcoop-etl:latest
  EOF

  tags = {
    Name = "greenandcoop-ec2"
  }
}
