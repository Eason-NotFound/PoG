import LoginForm from "@/components/login-form";
export default function Login() {
  return <LoginForm integration={process.env.POG_A2_INTEGRATION === "true"} />;
}
