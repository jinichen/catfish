use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub const EXPERT_BOTS_SCHEMA_VERSION: u32 = 2;
pub const DEFAULT_ADVISOR_PROFILE: &str = "catfish-advisor";
pub const MANAGED_PROFILE_MARKER: &str = ".catfish-managed-profile";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ModelPolicyMode {
    InheritPicker,
    Fixed,
}

impl Default for ModelPolicyMode {
    fn default() -> Self {
        Self::InheritPicker
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Default)]
#[serde(default)]
pub struct ModelPolicy {
    pub mode: ModelPolicyMode,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model_id: Option<String>,
}

impl ModelPolicy {
    pub fn validate(&mut self) -> Result<(), String> {
        match self.mode {
            ModelPolicyMode::InheritPicker => self.model_id = None,
            ModelPolicyMode::Fixed => {
                let model = self.model_id.as_deref().unwrap_or("").trim();
                if model.is_empty() {
                    return Err("固定模型策略必须选择模型".to_string());
                }
                self.model_id = Some(model.to_string());
            }
        }
        Ok(())
    }

    pub fn resolve_model(&self, picker_model: &str) -> String {
        match self.mode {
            ModelPolicyMode::InheritPicker => picker_model.trim().to_string(),
            ModelPolicyMode::Fixed => self.model_id.clone().unwrap_or_default(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(default)]
pub struct BotRegistration {
    pub enabled: bool,
    pub model_policy: ModelPolicy,
}

impl Default for BotRegistration {
    fn default() -> Self {
        Self {
            enabled: true,
            model_policy: ModelPolicy::default(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Default)]
#[serde(default)]
pub struct GatewayOwnership {
    pub enabled_multiplex: bool,
    pub allowlist_entries: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(default)]
pub struct ExpertBotsConfig {
    pub schema_version: u32,
    pub enabled: bool,
    pub registrations: BTreeMap<String, BotRegistration>,
    pub bindings: BTreeMap<String, String>,
    pub gateway_ownership: GatewayOwnership,
}

impl Default for ExpertBotsConfig {
    fn default() -> Self {
        Self {
            schema_version: EXPERT_BOTS_SCHEMA_VERSION,
            enabled: false,
            registrations: BTreeMap::new(),
            bindings: BTreeMap::new(),
            gateway_ownership: GatewayOwnership::default(),
        }
    }
}

pub const SUPPORTED_SCENARIOS: [(&str, &str); 2] = [
    ("briefing.advisor", "早安工作参谋"),
    ("email.draft", "邮件起草"),
];

pub fn is_supported_scenario(value: &str) -> bool {
    SUPPORTED_SCENARIOS.iter().any(|(id, _)| *id == value)
}

pub fn is_valid_profile_name(name: &str) -> bool {
    if name == "default" || name.is_empty() || name.len() > 64 {
        return false;
    }
    let mut chars = name.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    let syntax_ok = (first.is_ascii_lowercase() || first.is_ascii_digit())
        && chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-' || c == '_');
    let reserved = [
        "hermes", "default", "test", "tmp", "root", "sudo", "chat", "model",
        "gateway", "setup", "whatsapp", "login", "logout", "status", "cron", "doctor",
        "dump", "config", "pairing", "skills", "tools", "mcp", "sessions", "insights",
        "version", "update", "uninstall", "profile", "plugins", "honcho", "acp",
    ];
    syntax_ok && !reserved.contains(&name)
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ExpertBotsStatus {
    pub enabled: bool,
    pub ready: bool,
    pub advisor_profile: String,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ExpertScenarioSummary {
    pub id: String,
    pub label: String,
    pub profile_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ExpertBotSummary {
    pub id: String,
    pub display_name: String,
    pub description: String,
    pub managed_by_companion: bool,
    pub enabled: bool,
    pub ready: bool,
    pub reason: String,
    pub model_policy: ModelPolicy,
    pub configured_model: Option<String>,
    pub provider: Option<String>,
    pub skill_count: usize,
    pub bound_scenarios: Vec<String>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ExpertBotsSnapshot {
    pub enabled: bool,
    pub bots: Vec<ExpertBotSummary>,
    pub available_profiles: Vec<AvailableProfileSummary>,
    pub scenarios: Vec<ExpertScenarioSummary>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct AvailableProfileSummary {
    pub id: String,
    pub display_name: String,
    pub description: String,
    pub registered: bool,
    pub managed_by_companion: bool,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExpertBotCreateInput {
    pub id: String,
    pub display_name: String,
    pub description: String,
    #[serde(default)]
    pub soul: String,
    #[serde(default)]
    pub clone_from: Option<String>,
    #[serde(default)]
    pub model_policy: ModelPolicy,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExpertBotUpdateInput {
    pub id: String,
    pub display_name: Option<String>,
    pub description: Option<String>,
    pub soul: Option<String>,
    pub enabled: Option<bool>,
    pub model_policy: Option<ModelPolicy>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ExpertBotRoute {
    pub enabled: bool,
    pub ready: bool,
    pub profile_id: Option<String>,
    pub model: String,
    pub reason: String,
}
