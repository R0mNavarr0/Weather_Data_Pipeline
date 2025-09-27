output "ec2_public_ip" {
  description = "Adresse IP publique de l'instance"
  value       = aws_instance.app_ec2.public_ip
}