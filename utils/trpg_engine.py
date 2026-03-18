import random

class TRPGEngine:
    @staticmethod
    def roll_d20(bonus=0):
        """擲 1d20 + 加成"""
        return random.randint(1, 20) + bonus

    @staticmethod
    def get_scaling_factor(level):
        """
        計算經驗增幅次方項。
        """
        return 1 + (level * 0.01)
    
    @staticmethod
    def calculate_combat(player, monster, job_config):
        """
        傳入 job_config 參數字典，不再依賴 job 名稱判斷
        job_config 應包含: crit_chance, crit_mult, flat_bonus_atk, hp_cost_per_atk
        """
        p_atk = player["attributes"]["STR"]
        
        # 1. 處理扣血換傷 (血色祭司類)
        hp_cost = job_config.get("hp_cost_per_atk", 0)
        atk_bonus = job_config.get("flat_bonus_atk", 0)
        
        if hp_cost > 0 and player['health'] > hp_cost:
            player['health'] -= hp_cost
        else:
            atk_bonus = 0 # 若血量不足不扣血也不給加成
            
        # 2. 處理暴擊 (破壁者類)
        crit_mult = 1.0
        if random.random() < job_config.get("crit_chance", 0):
            # 只有當玩家攻擊力大於怪物防禦時觸發 (保留原本邏輯)
            if p_atk > monster.get("def", 0):
                crit_mult = job_config.get("crit_mult", 1.5)

        # 3. 傷害計算公式
        # 修改: 將隨機波動與加成納入計算
        base_dmg = (p_atk // 1.5) * crit_mult
        player_dmg = max(1, int(base_dmg + random.randint(1, 6) + (int(atk_bonus*player['attributes']['INT']/10)) - monster['def']))
        monster['hp'] -= player_dmg
        
        # 4. 怪物反擊
        monster_dmg = 0
        if monster['hp'] > 0:
            # 敏捷減傷公式優化
            monster_dmg = max(1, monster['atk'] - (player['attributes']['DEX'] // job_config.get("dodge_div", 4)))
            player['health'] -= monster_dmg
            
        return player_dmg, monster_dmg

    @staticmethod
    def get_required_exp(level):
        return int(level**TRPGEngine.get_scaling_factor(level)) * 50 

    @staticmethod
    def check_level_up(player):
        required = TRPGEngine.get_required_exp(player.get("level", 1))
        return player.get("exp", 0) >= required